"""Ce que l'entreprise règle elle-même, au lieu de subir une constante Python.

Trois réglages, une seule idée derrière (spec §10, amendement A14) : un chiffre qui relève d'une
décision commerciale ou d'un usage de métier n'a rien à faire dans le code. Chacun était pourtant
une constante, et chacune se lisait comme une vérité du produit alors qu'elle n'était vraie que
pour un artisan moyen qui n'existe pas.

1. **Le taux de chute** (`Covering.waste_ratio_bp`). C'est le chiffre qui rend un devis crédible
   auprès d'un homme de métier, et il alimente la surface à commander — donc la quantité facturée.
   `WASTE_RATIO_BY_PATTERN` provisionne 8 % en pose droite : un carreleur qui pose du grand format
   sait que c'est faux pour lui, et n'avait aucun moyen de le corriger.
2. **Les défauts commerciaux** (`organization.default_*`). Délai de paiement, durée de validité,
   pénalités, indemnité de recouvrement : ce sont des mentions **obligatoires**, elles étaient
   réglables par devis et jamais par entreprise. `docs/strategie-produit.md` §2 demande l'inverse
   en toutes lettres.
3. **Les seuils de conformité** (`organization.inspection_thresholds`) et **la durée de l'essai**
   (`plan_catalog.trial_days`). L'amendement A12 refusait tout seuil venu d'une requête en
   promettant qu'« un réglage par organisation est une ligne SQL » : il n'existait aucune colonne
   où écrire cette ligne, et la porte de sortie était donc fictive.

Chaque test vérifie les **deux** sens : le réglage est pris en compte quand il est là, et le repli
est exactement l'ancienne valeur quand il ne l'est pas. Le second sens compte autant que le
premier — c'est lui qui garantit qu'aucune fixture du métré n'a eu à bouger.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.quotes import CommercialDefaults
from app.geometry.quantities import WASTE_RATIO_BY_PATTERN, build_takeoff
from app.intelligence.ergonomy import (
    DEFAULT_THRESHOLDS,
    OVERRIDABLE_THRESHOLDS,
    thresholds_from,
)
from app.models.billing import (
    DEFAULT_LATE_PENALTY_RATE_BP,
    DEFAULT_PAYMENT_DAYS,
    DEFAULT_RECOVERY_INDEMNITY_CENTS,
    DEFAULT_VALIDITY_DAYS,
)
from app.models.billing_plan import PlanCatalog, Subscription, SubscriptionStatus
from app.models.organization import Organization
from app.services.seed_plans import PLAN_ARTISAN, PLAN_BUSINESS, TRIAL_PLAN_CODE
from tests.conftest import subscribe

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


# --- 1. Le taux de chute -------------------------------------------------------------------------


def _scene_with(covering: dict[str, Any]) -> dict[str, Any]:
    """Scene graph minimal : une face de mur de 4 m x 2,5 m, revêtue de ce qu'on lui passe.

    Écrit à la main plutôt que tiré d'une fixture : les fixtures du métré font foi et ne doivent
    pas bouger (`CLAUDE.md`), or ce test a besoin d'une donnée que, par construction, aucune d'elles
    ne porte.
    """
    return {
        "units": "cm",
        "project_id": 1,
        "rooms": [
            {
                "id": 1,
                "name": "Salon",
                "ceiling_height_cm": 250.0,
                "wall_thickness_cm": 10.0,
                "outline": [[0, 0], [400, 0], [400, 300], [0, 300]],
                "net_floor_area_cm2": 111_800.0,
                "nodes": [
                    {
                        "kind": "wall",
                        "face_id": 1,
                        "face_label": "A",
                        "length_cm": 400.0,
                        "height_cm": 250.0,
                        "holes": [],
                        "covering": covering,
                    }
                ],
            }
        ],
    }


def _tiling_of(covering: dict[str, Any]) -> dict[str, Any]:
    takeoff = build_takeoff(_scene_with(covering))
    tiling: dict[str, Any] = takeoff["rooms"][0]["faces"][0]["tiling"]
    return tiling


CARRELAGE = {"material": "carrelage", "unit_width_cm": 60.0, "unit_height_cm": 60.0}


def test_a_covering_without_a_declared_waste_falls_back_on_the_pattern() -> None:
    """Le repli est exactement la table d'avant : aucune fixture du métré n'a eu à bouger."""
    tiling = _tiling_of(CARRELAGE)

    assert tiling["waste_ratio"] == WASTE_RATIO_BY_PATTERN["straight"] == 0.08
    # 4 m x 2,5 m = 10 m², plus 8 % = 10,8 m² à commander.
    assert tiling["ordered_area_m2"] == 10.8


def test_the_tiler_who_lays_large_format_orders_what_he_really_needs() -> None:
    """Le réglage change la quantité **facturée**, et c'est tout l'objet du correctif.

    15 % au lieu de 8 % sur 10 m² : 11,5 m² à commander au lieu de 10,8, soit deux carreaux de plus
    à sortir de la palette. C'est exactement l'écart qu'un carreleur reprochait au devis.
    """
    tiling = _tiling_of({**CARRELAGE, "waste_ratio_bp": 1_500})

    assert tiling["waste_ratio"] == 0.15
    assert tiling["ordered_area_m2"] == 11.5
    assert tiling["units_total"] == 32


def test_an_unreadable_waste_ratio_falls_back_and_says_so() -> None:
    """Un métré qui rattrape en silence est un métré dont on ne peut plus expliquer le total.

    Trois entrées fautives, un même comportement : la provision du motif s'applique, et
    `warnings` porte la trace. Le `True` n'est pas une coquetterie — `bool` est un `int` en Python,
    et sans filtre il aurait valu un point de base, soit un taux de chute de 0,01 %.
    """
    for valeur in (-100, 20_000, "beaucoup", True):
        takeoff = build_takeoff(_scene_with({**CARRELAGE, "waste_ratio_bp": valeur}))
        tiling = takeoff["rooms"][0]["faces"][0]["tiling"]

        assert tiling["waste_ratio"] == WASTE_RATIO_BY_PATTERN["straight"], valeur
        assert any("taux de chute" in message for message in takeoff["warnings"]), valeur


def test_two_faces_at_two_waste_ratios_are_two_lines_of_the_order() -> None:
    """Le regroupement prend la chute dans sa clé, faute de quoi il afficherait un taux faux.

    Les surfaces à commander restaient justes — elles sont sommées face par face — mais la colonne
    « taux de chute » du groupe portait celle de la première face rencontrée. Une ligne de commande
    dont le taux n'explique pas la quantité est pire qu'une ligne de plus.
    """
    scene = _scene_with(CARRELAGE)
    ordinaire = scene["rooms"][0]["nodes"][0]
    grand_format = {
        **ordinaire,
        "face_id": 2,
        "face_label": "B",
        "covering": {**CARRELAGE, "waste_ratio_bp": 1_500},
    }
    scene["rooms"][0]["nodes"].append(grand_format)

    groupes = build_takeoff(scene)["rooms"][0]["coverings"]

    assert len(groupes) == 2
    assert sorted(groupe["waste_ratio"] for groupe in groupes) == [0.08, 0.15]


async def test_the_waste_ratio_travels_from_the_editor_to_the_takeoff(
    auth_client: AsyncClient,
) -> None:
    """Le chemin réel, de bout en bout : la face est enregistrée, le métré la relit.

    Le test précédent porte sur la fonction pure ; celui-ci prouve que le champ traverse le schéma
    d'entrée (`extra="forbid"` l'aurait refusé), la colonne JSON et le scene graph.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    room = (
        await auth_client.post(
            f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
        )
    ).json()
    face = next(entry for entry in room["faces"] if entry["kind"] == "floor")

    enregistre = await auth_client.patch(
        f"/api/faces/{face['id']}",
        json={
            "covering": {
                "material": "carrelage",
                "unit_width_cm": 60,
                "unit_height_cm": 60,
                "waste_ratio_bp": 1_500,
            }
        },
    )
    assert enregistre.status_code == 200, enregistre.text

    takeoff = (await auth_client.get(f"/api/projects/{project_id}/takeoff")).json()
    sol = next(
        entry for entry in takeoff["rooms"][0]["faces"] if entry["face_id"] == face["id"]
    )
    assert sol["tiling"]["waste_ratio"] == 0.15


# --- 2. Les défauts commerciaux de l'entreprise --------------------------------------------------


def test_a_missing_commercial_default_falls_back_on_the_regulatory_constant() -> None:
    """`NULL` veut dire « prends le défaut du produit », jamais zéro.

    Les confondre écrirait « paiement à 0 jour » sur une facture, ce qu'aucun code de commerce ne
    prévoit. Une organisation absente — un devis dont l'entreprise a disparu — donne la même chose.
    """
    for organisation in (None, Organization(name="Sans réglage", slug="sans-reglage")):
        defauts = CommercialDefaults(organisation)

        assert defauts.payment_days == DEFAULT_PAYMENT_DAYS == 30
        assert defauts.validity_days == DEFAULT_VALIDITY_DAYS == 90
        assert defauts.late_penalty_rate_bp == DEFAULT_LATE_PENALTY_RATE_BP
        assert defauts.recovery_indemnity_cents == DEFAULT_RECOVERY_INDEMNITY_CENTS
        assert defauts.text_defaults() == {}


def test_a_zero_default_is_a_value_and_not_an_absence() -> None:
    """Une entreprise qui exige le paiement comptant écrit 0, et 0 doit être appliqué.

    C'est le cas limite qui distingue un repli sur `is None` d'un repli sur la valeur fausse : un
    `or` aurait ramené les 30 jours du code de commerce sur une entreprise qui ne les accorde pas.
    """
    defauts = CommercialDefaults(
        Organization(name="Comptant", slug="comptant", default_payment_days=0)
    )

    assert defauts.payment_days == 0


async def test_the_company_writes_its_terms_once_and_every_quote_carries_them(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le scénario réel : 45 jours accordés au donneur d'ordre, écrits une seule fois.

    Le devis les porte sans que personne les ressaisisse, et la facture qui en découle échoit
    45 jours plus tard et non 30. C'est très exactement ce que `docs/strategie-produit.md` §2
    réclame, et ce que le produit ne savait pas faire.
    """
    project_id, organization_id = await _project_with_room(auth_client)

    organisation = (
        await session.execute(
            select(Organization).where(col(Organization.id) == organization_id)
        )
    ).scalar_one()
    organisation.default_payment_days = 45
    organisation.default_validity_days = 30
    organisation.default_late_penalty_rate_bp = 1_200
    organisation.default_recovery_indemnity_cents = 5_000
    organisation.default_payment_terms = "Acompte de 30 % à la commande, solde à la réception."
    organisation.default_mediator_name = "Médiation de la consommation - CM2C"
    await session.commit()

    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )
    assert devis.status_code == 201, devis.text
    corps = devis.json()
    assert corps["late_penalty_rate_bp"] == 1_200
    assert corps["recovery_indemnity_cents"] == 5_000
    assert corps["payment_terms"].startswith("Acompte de 30 %")
    assert corps["mediator_name"] == "Médiation de la consommation - CM2C"
    # 30 jours de validité et non 90 : la date est celle que l'entreprise a réglée.
    assert _days_until(corps["valid_until"]) == 30

    quote_id = corps["id"]
    assert (await auth_client.post(f"/api/quotes/{quote_id}/issue")).status_code == 200
    assert (
        await auth_client.patch(f"/api/quotes/{quote_id}", json={"status": "accepted"})
    ).status_code == 200
    facture = await auth_client.post(f"/api/quotes/{quote_id}/invoice")
    assert facture.status_code == 200, facture.text

    assert _days_until(facture.json()["due_date"]) == 45


async def test_the_quote_may_still_derogate_from_the_company_defaults(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Un devis déroge aux conditions générales de l'entreprise ; l'inverse n'aurait aucun sens.

    L'ordre est donc : saisie du document > défaut de l'entreprise > constante. Sans lui, régler un
    défaut d'entreprise rendrait la saisie par devis inopérante, c'est-à-dire remplacerait un
    manque par une régression.
    """
    project_id, organization_id = await _project_with_room(auth_client)
    organisation = (
        await session.execute(
            select(Organization).where(col(Organization.id) == organization_id)
        )
    ).scalar_one()
    organisation.default_recovery_indemnity_cents = 5_000
    await session.commit()

    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"client_name": "Madame Dupont", "recovery_indemnity_cents": 4_000},
    )

    assert devis.status_code == 201, devis.text
    assert devis.json()["recovery_indemnity_cents"] == 4_000


# --- 3. Les seuils de conformité et la durée de l'essai ------------------------------------------


def test_only_the_thresholds_the_report_republishes_can_be_overridden() -> None:
    """On ne règle que ce qu'on peut relire, et c'est une égalité, pas une inclusion.

    Un seuil réglable mais non republié serait un réglage dont personne ne pourrait vérifier
    l'effet ; un seuil republié mais non réglable serait la promesse en l'air que l'amendement A12
    faisait. `accessible` est hors liste : c'est un mode demandé requête par requête, pas un
    réglage d'entreprise, et il ne relâche aucun seuil — il en resserre.
    """
    assert set(DEFAULT_THRESHOLDS.to_dict()) - {"accessible"} == OVERRIDABLE_THRESHOLDS
    assert "accessible" not in OVERRIDABLE_THRESHOLDS


def test_an_empty_override_gives_exactly_the_product_defaults() -> None:
    """Le repli, encore : une organisation qui n'a rien réglé est inspectée comme avant."""
    assert thresholds_from(None).to_dict() == DEFAULT_THRESHOLDS.to_dict()
    assert thresholds_from({}).to_dict() == DEFAULT_THRESHOLDS.to_dict()


def test_an_unusable_override_is_ignored_and_never_fatal() -> None:
    """Ces valeurs arrivent par `psql`. Une faute de frappe est un réglage raté, pas une panne.

    Faire échouer chaque inspection sur une clé inconnue transformerait l'une en l'autre.
    L'opérateur n'est pas laissé sans retour : le rapport republie les seuils **appliqués**, et il
    y lit immédiatement que sa ligne n'a rien changé.

    Zéro est refusé au même titre qu'un négatif : un seuil de passage nul rendrait conforme un
    couloir inexistant, ce qui est exactement l'abus que A12 écarte.
    """
    seuils = thresholds_from(
        {
            "cle_inconnue": 42,
            "passage_min_cm": "quatre-vingt-dix",
            "passage_blocking_cm": 0,
            "ceiling_height_min_cm": -10,
            "door_clear_width_min_cm": True,
        }
    )

    assert seuils.to_dict() == DEFAULT_THRESHOLDS.to_dict()


def test_a_real_override_is_applied_and_republished() -> None:
    """Le sens utile : l'entreprise qui travaille sous une autre norme la fait appliquer."""
    seuils = thresholds_from({"passage_min_cm": 100.0, "ceiling_height_min_cm": 240.0})

    assert seuils.passage_min_cm == 100.0
    assert seuils.ceiling_height_min_cm == 240.0
    # Les seuils non cités gardent leur valeur : une surcharge partielle est une surcharge.
    assert seuils.door_clear_width_min_cm == DEFAULT_THRESHOLDS.door_clear_width_min_cm


async def test_the_inspection_applies_the_thresholds_written_in_database(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """La ligne SQL que l'amendement A12 promettait, exécutée pour de bon.

    Le rapport republie les seuils appliqués, et c'est là que se lit la preuve : sans colonne, cette
    valeur ne pouvait provenir que de la dataclass, donc valoir 90. La règle « aucun seuil n'entre
    par le corps d'une requête » n'a pas bougé d'un mot — elle est simplement devenue tenable.
    """
    project_id, organization_id = await _project_with_room(auth_client)

    avant = await auth_client.get(f"/api/projects/{project_id}/inspection")
    assert avant.status_code == 200, avant.text
    assert avant.json()["thresholds"]["passage_min_cm"] == 90.0

    organisation = (
        await session.execute(
            select(Organization).where(col(Organization.id) == organization_id)
        )
    ).scalar_one()
    organisation.inspection_thresholds = {"passage_min_cm": 110.0}
    await session.commit()

    apres = await auth_client.get(f"/api/projects/{project_id}/inspection")
    assert apres.json()["thresholds"]["passage_min_cm"] == 110.0
    # Le mode accessible reste décidé par la requête, et il l'emporte : il resserre.
    accessible = await auth_client.get(
        f"/api/projects/{project_id}/inspection", params={"accessible": "true"}
    )
    assert accessible.json()["thresholds"]["accessible"] is True
    assert accessible.json()["thresholds"]["passage_min_cm"] == 110.0


async def test_a_threshold_still_never_enters_through_the_request_body(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """La contrepartie de la colonne : la porte de sortie ouverte n'ouvre pas la porte d'entrée.

    L'entreprise est abonnée au palier qui accorde l'aménagement automatique, sans quoi le refus
    observé serait celui du mur de paiement — 402 avant même la validation du corps — et le test
    cesserait de dire quoi que ce soit sur les seuils.
    """
    project_id, organization_id = await _project_with_room(auth_client)
    await subscribe(session, organization_id, PLAN_BUSINESS)
    rooms = (await auth_client.get(f"/api/projects/{project_id}")).json()["rooms"]

    refus = await auth_client.post(
        f"/api/rooms/{rooms[0]["id"]}/layouts", json={"passage_min_cm": 10}
    )

    assert refus.status_code == 422, refus.text


async def test_the_trial_duration_is_a_column_of_the_catalog(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Allonger l'essai pour une campagne est un `UPDATE`, pas un déploiement.

    La page tarifs et `start_trial` lisent la **même** valeur : les faire diverger afficherait
    « 30 jours » à un prospect qui en obtiendrait quatorze.
    """
    assert (await auth_client.get("/api/plans")).json()["trial_days"] == 14

    palier = (
        await session.execute(
            select(PlanCatalog).where(col(PlanCatalog.code) == TRIAL_PLAN_CODE)
        )
    ).scalar_one()
    palier.trial_days = 30
    await session.commit()

    assert (await auth_client.get("/api/plans")).json()["trial_days"] == 30

    project_id, organization_id = await _project_with_room(auth_client)
    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )
    assert devis.status_code == 201, devis.text

    abonnement = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().one()
    assert abonnement.plan_code == PLAN_ARTISAN
    assert abonnement.status is SubscriptionStatus.TRIALING
    assert abonnement.trial_ends_at is not None
    assert 29 <= _days_until(abonnement.trial_ends_at.isoformat()) <= 30


async def test_a_trial_duration_of_zero_offers_no_trial_at_all(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Zéro est un réglage licite — « plus d'essai » — et pas une panne.

    Sans ce cas, la colonne aurait produit une période d'abonnement de durée nulle, que la
    contrainte `current_period_end > current_period_start` refuse en base : le premier geste
    monétisé d'un compte neuf serait devenu une erreur 500.
    """
    project_id, _organization_id = await _project_with_room(auth_client)
    palier = (
        await session.execute(
            select(PlanCatalog).where(col(PlanCatalog.code) == TRIAL_PLAN_CODE)
        )
    ).scalar_one()
    palier.trial_days = 0
    await session.commit()

    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )

    assert devis.status_code == 402, devis.text
    assert devis.json()["code"] == "feature_required"


def _days_until(moment: str) -> int:
    """Nombre de jours entiers d'ici à une date ISO, arrondi au plus proche."""
    parsed = datetime.fromisoformat(moment)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round((parsed - datetime.now(UTC)) / timedelta(days=1))


async def _project_with_room(client: AsyncClient) -> tuple[int, int]:
    """Un chantier d'une pièce, et l'organisation personnelle qui le porte."""
    project_id = int(
        (await client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    created = await client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
    )
    assert created.status_code == 201, created.text
    organization_id = int((await client.get("/api/organizations")).json()[0]["id"])
    return project_id, organization_id
