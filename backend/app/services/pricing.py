"""Du métré au devis : correspondance revêtement → ligne de barème, puis chiffrage.

Fonctions **pures**, sans base de données ni entrée-sortie : elles ne lisent que la sortie de
`app.geometry.quantities.build_takeoff` et des instantanés de barème. C'est ce qui permet de les
vérifier ligne à ligne, et c'est ce qui garantit qu'un devis se recalcule à l'identique.

Le module répond à une question précise, posée par `docs/strategie-produit.md` §3.1 : *comment
éviter les soixante rattachements à la main d'un projet de douze pièces ?* La réponse tient en
quatre paliers, du plus explicite au plus général :

1. un rattachement de face (`face_costing`), quand l'artisan a décidé pour cette face-là ;
2. le matériau du revêtement, lu comme un code de barème s'il en porte un ;
3. le matériau du revêtement, traduit par la table de synonymes du métier — « faïence »,
   « carrelage mural » et « carrelage » sur un mur désignent la même ligne de prix ;
4. le code par défaut demandé à la création du devis, par nature de face.

Sans correspondance à l'issue des quatre, la face **ne produit aucune ligne** et un avertissement
la nomme. Inventer un prix par défaut serait la pire des issues : le devis aurait l'air complet.

Deux règles d'arithmétique, et elles ne sont pas cosmétiques :

- **Tout se calcule en `Decimal` et se stocke en centimes entiers.** L'arrondi d'une ligne est
  fait une fois, au demi supérieur, et figé.
- **La TVA se calcule par taux, sur la somme des bases, et non ligne à ligne.** C'est la méthode
  des documents comptables français : additionner des TVA déjà arrondies fait dériver le total de
  quelques centimes, et c'est exactement ce que le comptable du client remarque.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.billing import BASIS_POINTS, FRENCH_RENOVATION_VAT_RATES_BP, PriceUnit

WALL = "wall"
FLOOR = "floor"
CEILING = "ceiling"

# Codes du barème par défaut auxquels le moteur sait recourir de lui-même (voir
# `app/services/seed_prices.py`). Ils ne sont pas obligatoires : un barème qui ne les contient pas
# produit simplement un devis sans plinthe ni corniche, avec un avertissement.
SKIRTING_CODE = "PLINTHE"
CORNICE_CODE = "CORNICHE"
DOOR_CODE = "POSE-PORTE"
WINDOW_CODE = "POSE-FENETRE"

# Synonymes du métier, par nature de face. La même saisie n'y désigne pas la même ligne de prix
# selon l'endroit : « carrelage » sur un mur est de la faïence, au sol c'est du carrelage de sol,
# et les deux n'ont ni le même prix ni la même mise en œuvre.
MATERIAL_ALIASES: dict[str, dict[str, str]] = {
    WALL: {
        "peinture": "PEINT-MUR",
        "peinture-murale": "PEINT-MUR",
        "peinture-acrylique": "PEINT-MUR",
        "faience": "FAIENCE",
        "carrelage": "FAIENCE",
        "carrelage-mural": "FAIENCE",
        "papier-peint": "PAPIER-PEINT",
        "toile-de-verre": "TOILE-VERRE",
        "enduit": "ENDUIT-MUR",
        "placo": "PLACO-MUR",
        "plaque-de-platre": "PLACO-MUR",
        "lambris": "LAMBRIS",
    },
    FLOOR: {
        "carrelage": "CARRELAGE-SOL",
        "carrelage-sol": "CARRELAGE-SOL",
        "parquet": "PARQUET",
        "stratifie": "STRATIFIE",
        "lino": "SOUPLE-SOL",
        "linoleum": "SOUPLE-SOL",
        "pvc": "SOUPLE-SOL",
        "vinyle": "SOUPLE-SOL",
        "moquette": "MOQUETTE",
        "ragreage": "RAGREAGE",
    },
    CEILING: {
        "peinture": "PEINT-PLAF",
        "peinture-plafond": "PEINT-PLAF",
        "dalle": "DALLE-PLAFOND",
        "dalles": "DALLE-PLAFOND",
        "faux-plafond": "DALLE-PLAFOND",
        "placo": "PLACO-MUR",
        "lambris": "LAMBRIS",
    },
}

CENT = Decimal("1")
QUANTITY_EXPONENT = Decimal("0.001")


@dataclass(frozen=True)
class PriceReference:
    """Instantané d'une ligne de barème, tel que le moteur le consomme.

    Un objet plat et non le modèle SQLModel : le chiffrage ne doit connaître ni session, ni
    chargement paresseux. C'est aussi ce qui rend évident que la ligne produite est une **copie**.
    """

    code: str
    label: str
    unit: str
    unit_price_cents: int
    vat_rate_bp: int


@dataclass(frozen=True)
class CostingOverride:
    """Décision explicite de l'artisan sur une face (`face_costing`)."""

    price_item_code: str | None = None
    quantity: Decimal | None = None
    unit_price_cents: int | None = None


@dataclass(frozen=True)
class PricingOptions:
    """Ce que la demande de devis choisit, et que le métré ne peut pas deviner.

    `default_price_codes` est le « tous les murs en peinture » d'un clic : c'est lui qui rend la
    fonctionnalité utilisable en production plutôt que belle en démonstration.
    """

    default_price_codes: dict[str, str] = field(default_factory=dict)
    include_skirting: bool = True
    include_cornice: bool = False
    include_openings: bool = False


@dataclass(frozen=True)
class ProposedLine:
    """Une ligne prête à être écrite dans `quote_line`. Tous les montants sont en centimes."""

    label: str
    unit: str
    quantity: Decimal
    unit_price_cents: int
    vat_rate_bp: int
    total_ht_cents: int
    source_face_id: int | None = None
    source_price_item_code: str | None = None


@dataclass(frozen=True)
class VatBucket:
    """Une assiette de TVA : un taux, sa base HT et la taxe correspondante."""

    rate_bp: int
    base_cents: int
    tax_cents: int


@dataclass(frozen=True)
class QuotePlan:
    """Le devis proposé, avant écriture. `warnings` n'est pas décoratif : voir la docstring."""

    lines: tuple[ProposedLine, ...]
    vat_breakdown: tuple[VatBucket, ...]
    total_ht_cents: int
    total_tva_cents: int
    total_ttc_cents: int
    warnings: tuple[str, ...]


# --- Normalisation -------------------------------------------------------------------------------


def normalize_material(value: str | None) -> str:
    """Forme canonique d'un libellé de matériau : sans accent, minuscule, tirets simples.

    « Faïence », « FAIENCE » et « faience murale » doivent tomber sur la même entrée : un artisan
    ne saisit pas deux fois son revêtement de la même façon, et lui demander de le faire serait
    exactement la friction que ce module existe pour supprimer.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    reduced = "".join(char if char.isalnum() else "-" for char in ascii_only.lower())
    return "-".join(part for part in reduced.split("-") if part)


def resolve_price_code(
    kind: str,
    material: str | None,
    override: CostingOverride | None,
    options: PricingOptions,
    references: dict[str, PriceReference],
) -> str | None:
    """Code de barème retenu pour une face, ou `None` s'il n'y en a pas.

    L'ordre est le contrat du module :

    1. le rattachement explicite de la face — il l'emporte toujours, et il est renvoyé même si le
       barème ne le contient pas, pour que l'avertissement nomme le code que l'artisan a choisi ;
    2. le synonyme du métier, s'il désigne une ligne existante du barème ;
    3. le matériau lu tel quel comme un code — un artisan qui saisit ses propres références dans
       le plan ne doit pas en être empêché ;
    4. le code par défaut demandé pour cette nature de face ;
    5. à défaut, le code cherché en (2) ou (3), pour que l'avertissement soit nommant.
    """
    if override is not None and override.price_item_code:
        return override.price_item_code

    normalized = normalize_material(material)
    alias = MATERIAL_ALIASES.get(kind, {}).get(normalized) if normalized else None
    as_code = normalized.upper() if normalized else None

    if alias is not None and alias in references:
        return alias
    if as_code is not None and as_code in references:
        return as_code

    return options.default_price_codes.get(kind) or alias or as_code


# --- Arithmétique --------------------------------------------------------------------------------


def as_quantity(value: float | int | Decimal | None) -> Decimal:
    """Quantité au millième, en `Decimal`.

    Le métré rend des flottants (des m² arrondis à trois décimales) : la conversion passe par
    `str` et non par `Decimal(float)`, qui ramènerait la représentation binaire exacte — 0,1
    deviendrait 0,1000000000000000055511151231257827.
    """
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(QUANTITY_EXPONENT, rounding=ROUND_HALF_UP)


def line_total_cents(quantity: Decimal, unit_price_cents: int) -> int:
    """Montant HT d'une ligne, arrondi au centime le plus proche (demi supérieur).

    Un seul arrondi, ici, et il est ensuite figé dans `quote_line.total_ht_cents` : recalculer ce
    produit à chaque lecture ferait dépendre un document contractuel de la version du code.
    """
    return int((quantity * Decimal(unit_price_cents)).quantize(CENT, rounding=ROUND_HALF_UP))


def vat_buckets_from(pairs: Iterable[tuple[int, int]]) -> tuple[VatBucket, ...]:
    """Assiettes de TVA à partir de couples (taux en points de base, montant HT en centimes).

    La taxe est calculée sur la **somme** des bases d'un taux, et non ligne à ligne : additionner
    des TVA déjà arrondies fait dériver le total de quelques centimes sur un devis un peu fourni,
    et c'est le genre d'écart qui fait refaire le document.

    La signature travaille sur des couples et non sur des lignes typées : c'est le seul endroit du
    dépôt où cet arrondi est fait, et le document Factur-X doit pouvoir s'en servir sans importer
    le modèle du moteur de devis. Deux implémentations de la même règle finiraient par diverger.
    """
    bases: dict[int, int] = {}
    for rate_bp, total_ht_cents in pairs:
        bases[rate_bp] = bases.get(rate_bp, 0) + total_ht_cents

    return tuple(
        VatBucket(
            rate_bp=rate_bp,
            base_cents=base_cents,
            tax_cents=int(
                (Decimal(base_cents) * Decimal(rate_bp) / Decimal(BASIS_POINTS)).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
            ),
        )
        for rate_bp, base_cents in sorted(bases.items())
    )


def vat_breakdown(lines: tuple[ProposedLine, ...]) -> tuple[VatBucket, ...]:
    """Assiettes de TVA des lignes proposées, une par taux, triées par taux croissant."""
    return vat_buckets_from((line.vat_rate_bp, line.total_ht_cents) for line in lines)


def summarize(lines: tuple[ProposedLine, ...], warnings: tuple[str, ...] = ()) -> QuotePlan:
    """Assemble un plan de devis à partir de lignes déjà chiffrées."""
    buckets = vat_breakdown(lines)
    total_ht = sum(line.total_ht_cents for line in lines)
    total_tva = sum(bucket.tax_cents for bucket in buckets)
    return QuotePlan(
        lines=lines,
        vat_breakdown=buckets,
        total_ht_cents=total_ht,
        total_tva_cents=total_tva,
        total_ttc_cents=total_ht + total_tva,
        warnings=warnings,
    )


# --- Construction des lignes ---------------------------------------------------------------------


def _quantity_for(unit: str, face: dict[str, Any]) -> Decimal | None:
    """Quantité que le métré fournit pour une face, selon l'unité de la ligne de prix.

    `None` signale une quantité non établissable — jamais zéro : une ligne à zéro passerait pour
    un choix commercial, alors que c'est une mesure manquante.
    """
    if unit == PriceUnit.LUMP_SUM.value or unit == PriceUnit.UNIT.value:
        return Decimal("1")
    if unit == PriceUnit.LINEAR_METER.value:
        length = face.get("length_m")
        return None if length is None else as_quantity(length)
    net_area = face.get("net_area_m2")
    return None if net_area is None else as_quantity(net_area)


def _face_label(room_name: str, face: dict[str, Any]) -> str:
    """« Salle de bains — mur B », ou « Salle de bains — sol »."""
    kind_labels = {WALL: "mur", FLOOR: "sol", CEILING: "plafond"}
    kind = kind_labels.get(str(face.get("kind")), str(face.get("kind")))
    label = face.get("face_label")
    return f"{room_name} — {kind} {label}" if kind == "mur" and label else f"{room_name} — {kind}"


def _line_from(
    reference: PriceReference,
    quantity: Decimal,
    suffix: str,
    *,
    unit_price_cents: int | None = None,
    face_id: int | None = None,
) -> ProposedLine:
    price = reference.unit_price_cents if unit_price_cents is None else unit_price_cents
    return ProposedLine(
        label=f"{reference.label} — {suffix}" if suffix else reference.label,
        unit=reference.unit,
        quantity=quantity,
        unit_price_cents=price,
        vat_rate_bp=reference.vat_rate_bp,
        total_ht_cents=line_total_cents(quantity, price),
        source_face_id=face_id,
        source_price_item_code=reference.code,
    )


def _room_linear_lines(
    room: dict[str, Any],
    references: dict[str, PriceReference],
    options: PricingOptions,
    warnings: list[str],
) -> list[ProposedLine]:
    """Plinthe et corniche : des linéaires de pièce, pas de face.

    Ils ne se déduisent d'aucune face prise isolément — la plinthe fait le tour de la pièce moins
    ce que les percements au sol lui prennent — donc ils sont portés ici.
    """
    lines: list[ProposedLine] = []
    room_name = str(room.get("name") or "Pièce")
    wanted = (
        (options.include_skirting, SKIRTING_CODE, "skirting_ml", "linéaire de plinthe"),
        (options.include_cornice, CORNICE_CODE, "cornice_ml", "linéaire de corniche"),
    )
    for enabled, code, key, human in wanted:
        if not enabled:
            continue
        reference = references.get(code)
        if reference is None:
            warnings.append(
                f"{human} de « {room_name} » non chiffré : le barème ne contient pas le code "
                f"« {code} »"
            )
            continue
        measured = room.get(key)
        if measured is None:
            warnings.append(
                f"{human} de « {room_name} » non chiffré : le métré n'a pas su l'établir"
            )
            continue
        quantity = as_quantity(measured)
        if quantity <= 0:
            continue
        lines.append(_line_from(reference, quantity, room_name))
    return lines


def _opening_lines(
    room: dict[str, Any],
    references: dict[str, PriceReference],
    warnings: list[str],
) -> list[ProposedLine]:
    """Pose des menuiseries, comptée à l'unité. Optionnelle : toutes ne sont pas à reprendre."""
    lines: list[ProposedLine] = []
    room_name = str(room.get("name") or "Pièce")
    wanted = ((DOOR_CODE, "door_count", "portes"), (WINDOW_CODE, "window_count", "fenêtres"))
    for code, key, human in wanted:
        count = int(room.get(key) or 0)
        if count <= 0:
            continue
        reference = references.get(code)
        if reference is None:
            warnings.append(
                f"pose des {human} de « {room_name} » non chiffrée : le barème ne contient pas le "
                f"code « {code} »"
            )
            continue
        lines.append(_line_from(reference, Decimal(count), room_name))
    return lines


def build_quote_lines(
    takeoff: dict[str, Any],
    references: dict[str, PriceReference],
    costings: dict[int, CostingOverride] | None = None,
    options: PricingOptions | None = None,
) -> QuotePlan:
    """Traduit un métré en lignes de devis chiffrées, pièce par pièce et face par face.

    `takeoff` est la sortie de `app.geometry.quantities.build_takeoff`, `references` un barème
    indexé par code, `costings` les rattachements explicites indexés par identifiant de face.

    **Les avertissements du métré sont repris tels quels et en tête.** Une surface que le métré
    n'a pas su établir devient ici une ligne absente : le total est alors partiel, et un appelant
    qui émet le devis sans lire `warnings` facture moins que le chantier.

    Une ligne par face, et non un regroupement par code : c'est la promesse produit — *le devis
    chiffré par mur* (`docs/strategie-produit.md` §1) — et c'est ce qui rend le document
    vérifiable contre les élévations cotées.
    """
    costings = costings or {}
    options = options or PricingOptions()
    warnings: list[str] = list(takeoff.get("warnings") or [])
    lines: list[ProposedLine] = []

    for room in takeoff.get("rooms") or []:
        room_name = str(room.get("name") or "Pièce")
        for face in room.get("faces") or []:
            face_id = face.get("face_id")
            override = costings.get(face_id) if face_id is not None else None
            code = resolve_price_code(
                str(face.get("kind")), face.get("material"), override, options, references
            )
            suffix = _face_label(room_name, face)

            if code is None:
                warnings.append(
                    f"{suffix} : aucun revêtement déclaré et aucun code par défaut — face non "
                    "chiffrée"
                )
                continue
            reference = references.get(code)
            if reference is None:
                warnings.append(
                    f"{suffix} : le barème ne contient pas le code « {code} » — face non chiffrée"
                )
                continue

            quantity = (
                override.quantity
                if override is not None and override.quantity is not None
                else _quantity_for(reference.unit, face)
            )
            if quantity is None:
                warnings.append(
                    f"{suffix} : quantité non établissable par le métré — face non chiffrée"
                )
                continue
            if quantity <= 0:
                continue

            lines.append(
                _line_from(
                    reference,
                    as_quantity(quantity),
                    suffix,
                    unit_price_cents=(
                        override.unit_price_cents if override is not None else None
                    ),
                    face_id=face_id,
                )
            )

        lines.extend(_room_linear_lines(room, references, options, warnings))
        if options.include_openings:
            lines.extend(_opening_lines(room, references, warnings))

    frozen = tuple(lines)
    warnings.extend(_unusual_vat_warnings(frozen))
    return summarize(frozen, tuple(warnings))


def _unusual_vat_warnings(lines: tuple[ProposedLine, ...]) -> list[str]:
    """Signale un taux de TVA hors des trois taux de la rénovation en métropole.

    Un avertissement et non un refus : la Corse et l'outre-mer connaissent d'autres taux, et
    bloquer une facture légitime serait pire qu'une coquille signalée. Mais 200 points de base
    saisis à la place de 2000 — 2 % au lieu de 20 % — se voit ici avant l'envoi.
    """
    known = FRENCH_RENOVATION_VAT_RATES_BP
    unusual = sorted({line.vat_rate_bp for line in lines if line.vat_rate_bp not in known})
    return [
        f"taux de TVA inhabituel en rénovation : {rate / 100:g} % — vérifiez la nature des travaux"
        for rate in unusual
    ]
