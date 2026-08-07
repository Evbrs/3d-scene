"""Développement d'une recette de mobilier en primitives concrètes (`docs/spec-complete.md` §4.1).

Le développement a lieu **côté serveur**, pas dans le viewer : la spec §3.1 est explicite, le
frontend « ne fait que traduire ce JSON en objets 3D — aucune logique métier côté client ».
C'est aussi ce qui rend la répétition (`repeat_*`, `auto`) testable par des fixtures.
"""

from dataclasses import dataclass
from typing import Any

AUTO = "auto"

AXES = ("x", "y", "z")

# Axe de révolution par défaut d'un cylindre : la verticale, comme un pied de table. Les cylindres
# couchés (poignée de porte, barre d'appui, tringle) le déclarent explicitement.
DEFAULT_AXIS = "y"


@dataclass(frozen=True)
class Primitive:
    """Une primitive développée, en centimètres, relative au centre du meuble."""

    type: str
    offset: tuple[float, float, float]
    size: tuple[float, float, float]
    color_slot: str
    color: str | None
    operation: str
    axis: str = DEFAULT_AXIS

    def to_dict(self, digits: int = 4) -> dict[str, Any]:
        return {
            "type": self.type,
            "offset": [round(value, digits) for value in self.offset],
            "size": [round(value, digits) for value in self.size],
            "color_slot": self.color_slot,
            "color": self.color,
            "operation": self.operation,
            "axis": self.axis,
        }


def _axis_centers(
    position: float | str, repeat: int, relative_size: float, gap: float
) -> list[float]:
    """Centres relatifs des copies d'une primitive répétée le long d'un axe.

    Le groupe de copies est **centré sur la position demandée**, et `auto` signifie simplement
    « centré sur le milieu de la boîte englobante », c'est-à-dire 0,5. Une seule formule couvre
    donc les deux cas, ce qui évite deux comportements divergents à maintenir.

    Exemple de la spec §4.1 (commode, 4 tiroirs, taille 0,18, jeu 0,02) : pas = 0,20 et centres
    = 0,20 / 0,40 / 0,60 / 0,80.
    """
    base = 0.5 if position == AUTO else float(position)
    if repeat <= 1:
        return [base]

    step = relative_size + gap
    first = base - (repeat - 1) * step / 2.0
    return [first + index * step for index in range(repeat)]


def resolve_variants(
    variants: list[dict[str, Any]] | None, variant_params: dict[str, Any] | None
) -> dict[tuple[str, str], int]:
    """Traduit les paramètres de variation d'une instance en répétitions à appliquer.

    Spec §4.4 : le paramètre de variation (nombre de tiroirs, d'étagères…) est « défini dans la
    recette du `FurnitureType`, pas dans le moteur de rendu générique ». La recette déclare donc
    ce qu'elle accepte — `{"name", "axis", "applies_to", "min", "max"}` — et l'instance ne fait
    que choisir une valeur. Une recette sans `variants` n'a aucun paramètre de variation : son
    `variant_params` est ignoré, faute de savoir ce qu'il pilote.

    Les emplacements visés sont désignés par leur `color_slot` et non par leur rang dans `parts` :
    un rang change dès qu'on insère une primitive dans la recette, un `color_slot` non.

    Renvoie les répétitions indexées par `(color_slot, axe)`.
    """
    if not variants or not variant_params:
        return {}

    resolved: dict[tuple[str, str], int] = {}
    for declaration in variants:
        repeat = _variant_repeat(declaration, variant_params)
        if repeat is None:
            continue
        axis = str(declaration.get("axis", DEFAULT_AXIS))
        if axis not in AXES:
            continue
        for color_slot in declaration.get("applies_to") or []:
            resolved[(str(color_slot), axis)] = repeat
    return resolved


def _variant_repeat(declaration: dict[str, Any], variant_params: dict[str, Any]) -> int | None:
    """Valeur retenue pour une variation, `None` si l'instance n'en propose pas d'utilisable.

    `variant_params` est un JSON libre, validé en amont sur sa seule taille : c'est ici, et
    nulle part ailleurs, que sa valeur rencontre les bornes de la recette. Elle est donc **bornée**
    et non refusée — refuser ferait disparaître un meuble du plan pour une saisie hors bornes,
    alors que le borner donne un résultat prévisible et corrigeable.

    `isinstance(True, int)` vaut `True` en Python : sans l'exclusion explicite des booléens, un
    `{"nb_tiroirs": true}` produirait un tiroir unique au lieu d'être ignoré.
    """
    raw = variant_params.get(str(declaration.get("name", "")))
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    low = int(declaration.get("min", 1))
    high = int(declaration.get("max", low))
    return max(low, min(high, raw))


def expand_recipe(
    parts: list[dict[str, Any]],
    size_cm: tuple[float, float, float],
    colors: dict[str, str],
    variants: list[dict[str, Any]] | None = None,
    variant_params: dict[str, Any] | None = None,
) -> list[Primitive]:
    """Développe une recette en primitives absolues (en cm, relatives au centre du meuble).

    `colors` ne contient que les emplacements choisis par l'instance ; les autres restent à
    `None`, charge au rendu d'appliquer son matériau par défaut. Inventer une couleur ici la
    rendrait indiscernable d'un choix explicite de l'utilisateur.

    `variants` (déclaré par la recette) et `variant_params` (choisi par l'instance) pilotent
    ensemble les répétitions : c'est ce qui fait du nombre de tiroirs un paramètre d'instance et
    non une géométrie codée en dur (spec §4.1).
    """
    width, height, depth = size_cm
    dimensions = (width, height, depth)
    overrides = resolve_variants(variants, variant_params)
    primitives: list[Primitive] = []

    for part in parts:
        relative_size = tuple(float(value) for value in part["rel_size"])
        absolute_size = (
            relative_size[0] * width,
            relative_size[1] * height,
            relative_size[2] * depth,
        )
        color_slot = str(part["color_slot"])
        repeats = tuple(
            overrides.get((color_slot, axis), int(part.get(f"repeat_{axis}", 1))) for axis in AXES
        )
        gap = float(part.get("gap", 0.0))
        # Une valeur inconnue retombe sur la verticale plutôt que d'être propagée telle quelle :
        # `parts` est du JSON libre, et le viewer n'a pas à arbitrer une chaîne fantaisiste.
        declared_axis = str(part.get("axis", DEFAULT_AXIS))
        revolution_axis = declared_axis if declared_axis in AXES else DEFAULT_AXIS

        centers_per_axis = [
            _axis_centers(part["rel_position"][axis], repeats[axis], relative_size[axis], gap)
            for axis in range(3)
        ]

        for center_x in centers_per_axis[0]:
            for center_y in centers_per_axis[1]:
                for center_z in centers_per_axis[2]:
                    centers = (center_x, center_y, center_z)
                    primitives.append(
                        Primitive(
                            type=str(part["type"]),
                            # Décalage par rapport au centre de la boîte englobante : une
                            # coordonnée relative de 0,5 tombe donc sur 0.
                            offset=tuple(  # type: ignore[arg-type]
                                (centers[axis] - 0.5) * dimensions[axis] for axis in range(3)
                            ),
                            size=absolute_size,
                            color_slot=color_slot,
                            color=colors.get(color_slot),
                            operation=str(part.get("operation", "add")),
                            axis=revolution_axis,
                        )
                    )

    return primitives


def requires_csg(primitives: list[Primitive]) -> bool:
    """Vrai si le meuble exige une opération booléenne (spec §4.2).

    Le frontend s'en sert pour n'activer `three-bvh-csg` — expérimental et coûteux (§3.2) — que
    sur les meubles qui en ont réellement besoin : vasque, baignoire, bac de douche.
    """
    return any(primitive.operation == "subtract" for primitive in primitives)
