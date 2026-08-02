"""Critères d'acceptation du ticket P5 (catalogue `FurnitureType` paramétrique).

Référence : `docs/spec-complete.md` §4 (mobilier générique paramétrique) et §7 (phase P5).
"""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import FurnitureType
from app.models.base import FurnitureCategory
from app.schemas.furniture import FurnitureTypeCreate
from app.services.catalog import CATALOG
from app.services.seed import catalog_slugs_missing_from, seed_catalog

# Tableau du catalogue cible (spec §4.3), recopié ici comme référence indépendante du code :
# le test doit échouer si une ligne de la spec disparaît du catalogue.
SPEC_CATALOG = {
    "general": [
        "porte battante",
        "porte coulissante",
        "fenêtre",
        "radiateur",
        "prise",
        "interrupteur",
        "applique",
        "suspension",
    ],
    "bathroom": [
        "vasque",
        "meuble sous-vasque",
        "baignoire",
        "bac de douche",
        "wc",
        "miroir",
        "colonne de rangement",
        "panier à linge",
        "barre d'appui",
    ],
    "bedroom": ["lit", "commode", "armoire", "table de chevet", "bureau"],
    "living_room": ["canapé", "table basse", "meuble tv", "bibliothèque"],
    "kitchen": ["meuble bas", "meuble haut", "îlot", "table", "chaise"],
}


# --- Le catalogue de référence -------------------------------------------------------------


def test_every_catalog_entry_is_a_valid_recipe() -> None:
    """Le catalogue est du code : une recette incohérente doit échouer ici, pas au rendu 3D."""
    for entry in CATALOG:
        FurnitureTypeCreate.model_validate(entry)


def test_catalog_slugs_are_unique() -> None:
    slugs = [entry["slug"] for entry in CATALOG]
    assert len(set(slugs)) == len(slugs)


@pytest.mark.parametrize(("category", "expected_count"), [
    (key, len(value)) for key, value in SPEC_CATALOG.items()
])
def test_the_catalog_covers_the_whole_spec_table(category: str, expected_count: int) -> None:
    """Spec §4.3 : chaque ligne du tableau = une entrée `FurnitureType`."""
    entries = [entry for entry in CATALOG if entry["category"].value == category]
    assert len(entries) >= expected_count, (
        f"catégorie {category} : {len(entries)} entrées pour {expected_count} attendues par la spec"
    )


def test_the_bathroom_pieces_that_need_csg_declare_a_subtraction() -> None:
    """Spec §4.2 : vasque et baignoire se construisent par opération booléenne."""
    for slug in ("vasque", "baignoire", "bac-de-douche"):
        entry = next(item for item in CATALOG if item["slug"] == slug)
        operations = {part.get("operation", "add") for part in entry["parts"]}
        assert "subtract" in operations, f"{slug} devrait déclarer une soustraction"


def test_the_commode_matches_the_spec_example() -> None:
    """La commode est l'exemple canonique de la spec §4.1 : elle doit rester fidèle."""
    commode = next(entry for entry in CATALOG if entry["slug"] == "commode")

    assert commode["color_slots"][:3] == ["corps", "facade", "poignee"]
    facade = next(part for part in commode["parts"] if part["color_slot"] == "facade")
    assert facade["repeat_y"] == 4, "le nombre de tiroirs est un paramètre, pas une géométrie figée"
    assert facade["rel_position"][1] == "auto"


def test_no_entry_references_an_undeclared_colour_slot() -> None:
    for entry in CATALOG:
        declared = set(entry["color_slots"])
        used = {part["color_slot"] for part in entry["parts"]}
        assert used <= declared, f"{entry['slug']} : {sorted(used - declared)}"


# --- Validation des recettes -------------------------------------------------------------------


def test_a_recipe_pointing_at_an_undeclared_slot_is_refused() -> None:
    with pytest.raises(ValidationError, match="non déclarés"):
        FurnitureTypeCreate.model_validate(
            {
                "slug": "faute-de-frappe",
                "name": "Faute de frappe",
                "category": FurnitureCategory.BEDROOM,
                "color_slots": ["corps"],
                "parts": [
                    {
                        "type": "box",
                        "rel_position": [0.5, 0.5, 0.5],
                        "rel_size": [1, 1, 1],
                        "color_slot": "crops",  # faute volontaire
                    }
                ],
            }
        )


def test_auto_without_repetition_is_refused() -> None:
    """`auto` répartit sur une répétition : sans répétition, la position est indéterminée."""
    with pytest.raises(ValidationError, match="auto"):
        FurnitureTypeCreate.model_validate(
            {
                "slug": "auto-sans-repetition",
                "name": "Auto sans répétition",
                "category": FurnitureCategory.BEDROOM,
                "color_slots": ["corps"],
                "parts": [
                    {
                        "type": "box",
                        "rel_position": [0.5, "auto", 0.5],
                        "rel_size": [1, 1, 1],
                        "color_slot": "corps",
                    }
                ],
            }
        )


def test_a_recipe_without_any_part_is_refused() -> None:
    with pytest.raises(ValidationError):
        FurnitureTypeCreate.model_validate(
            {
                "slug": "vide",
                "name": "Vide",
                "category": FurnitureCategory.BEDROOM,
                "color_slots": ["corps"],
                "parts": [],
            }
        )


def test_a_non_slug_identifier_is_refused() -> None:
    with pytest.raises(ValidationError):
        FurnitureTypeCreate.model_validate(
            {
                "slug": "Pas Un Slug",
                "name": "X",
                "category": FurnitureCategory.BEDROOM,
                "color_slots": ["corps"],
                "parts": [
                    {
                        "type": "box",
                        "rel_position": [0.5, 0.5, 0.5],
                        "rel_size": [1, 1, 1],
                        "color_slot": "corps",
                    }
                ],
            }
        )


# --- Chargement en base ---------------------------------------------------------------------


async def test_seeding_loads_the_whole_catalog(session: AsyncSession) -> None:
    report = await seed_catalog(session)

    assert report.created == len(CATALOG)
    assert await catalog_slugs_missing_from(session) == []

    stored = (await session.execute(select(FurnitureType))).scalars().all()
    assert len(stored) == len(CATALOG)


async def test_seeding_twice_creates_no_duplicate(session: AsyncSession) -> None:
    """Le seed doit être rejouable : au démarrage d'un environnement comme en déploiement."""
    await seed_catalog(session)
    second = await seed_catalog(session)

    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == len(CATALOG)

    stored = (await session.execute(select(FurnitureType))).scalars().all()
    assert len(stored) == len(CATALOG)


async def test_seeding_without_overwrite_preserves_local_changes(
    session: AsyncSession,
) -> None:
    await seed_catalog(session)
    commode = (
        await session.execute(select(FurnitureType).where(FurnitureType.slug == "commode"))
    ).scalar_one()
    commode.name = "Commode personnalisée"
    await session.commit()

    await seed_catalog(session, overwrite=False)

    session.expunge_all()
    reloaded = (
        await session.execute(select(FurnitureType).where(FurnitureType.slug == "commode"))
    ).scalar_one()
    assert reloaded.name == "Commode personnalisée"


# --- API du catalogue ---------------------------------------------------------------------------


async def test_the_catalog_is_readable_by_any_authenticated_user(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)

    response = await auth_client.get("/api/furniture-types", params={"limit": 200})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(CATALOG)
    assert {item["slug"] for item in body["items"]} == {entry["slug"] for entry in CATALOG}


async def test_the_catalog_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/furniture-types")).status_code == 401


async def test_the_catalog_can_be_filtered_by_category_and_name(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)

    by_category = await auth_client.get(
        "/api/furniture-types", params={"category": "bathroom", "limit": 200}
    )
    assert by_category.status_code == 200
    assert all(item["category"] == "bathroom" for item in by_category.json()["items"])
    assert by_category.json()["total"] >= 9

    by_name = await auth_client.get("/api/furniture-types", params={"search": "meuble"})
    assert by_name.status_code == 200
    assert all("meuble" in item["name"].lower() for item in by_name.json()["items"])


async def test_reading_a_single_entry_returns_its_recipe(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)

    response = await auth_client.get("/api/furniture-types/commode")

    assert response.status_code == 200
    body = response.json()
    assert body["color_slots"] == ["corps", "facade", "poignee"]
    assert any(part.get("repeat_y") == 4 for part in body["parts"])


async def test_an_unknown_entry_returns_404(auth_client: AsyncClient) -> None:
    assert (await auth_client.get("/api/furniture-types/inexistant")).status_code == 404


# --- Écriture réservée aux administrateurs --------------------------------------------------------

RECIPE = {
    "slug": "tabouret",
    "name": "Tabouret",
    "category": "kitchen",
    "color_slots": ["assise", "pied"],
    "parts": [
        {
            "type": "box",
            "rel_position": [0.5, 0.9, 0.5],
            "rel_size": [1, 0.15, 1],
            "color_slot": "assise",
        },
        {
            "type": "cylinder",
            "rel_position": ["auto", 0.4, 0.5],
            "rel_size": [0.1, 0.8, 0.1],
            "color_slot": "pied",
            "repeat_x": 3,
            "gap": 0.3,
        },
    ],
}


async def test_a_regular_user_cannot_write_the_shared_catalog(auth_client: AsyncClient) -> None:
    """Le catalogue est partagé par tous les projets : l'écriture doit être restreinte."""
    assert (await auth_client.post("/api/furniture-types", json=RECIPE)).status_code == 403
    assert (
        await auth_client.patch("/api/furniture-types/commode", json={"name": "X"})
    ).status_code == 403
    assert (await auth_client.delete("/api/furniture-types/commode")).status_code == 403


async def test_a_superuser_can_create_update_and_delete_an_entry(
    superuser_client: AsyncClient,
) -> None:
    created = await superuser_client.post("/api/furniture-types", json=RECIPE)
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == "tabouret"

    updated = await superuser_client.patch(
        "/api/furniture-types/tabouret", json={"name": "Tabouret haut"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Tabouret haut"

    assert (await superuser_client.delete("/api/furniture-types/tabouret")).status_code == 204
    assert (await superuser_client.get("/api/furniture-types/tabouret")).status_code == 404


async def test_creating_a_duplicate_slug_is_refused(superuser_client: AsyncClient) -> None:
    assert (await superuser_client.post("/api/furniture-types", json=RECIPE)).status_code == 201
    assert (await superuser_client.post("/api/furniture-types", json=RECIPE)).status_code == 409


async def test_an_invalid_recipe_is_refused_by_the_api(superuser_client: AsyncClient) -> None:
    broken = {**RECIPE, "slug": "casse", "parts": [
        {
            "type": "box",
            "rel_position": [0.5, 0.5, 0.5],
            "rel_size": [1, 1, 1],
            "color_slot": "emplacement-inconnu",
        }
    ]}
    assert (await superuser_client.post("/api/furniture-types", json=broken)).status_code == 422


async def test_an_update_cannot_desynchronise_slots_and_parts(
    superuser_client: AsyncClient,
) -> None:
    """Retirer un emplacement couleur encore utilisé par une primitive doit être refusé."""
    await superuser_client.post("/api/furniture-types", json=RECIPE)

    response = await superuser_client.patch(
        "/api/furniture-types/tabouret", json={"color_slots": ["assise"]}
    )
    assert response.status_code == 422
    assert "pied" in response.json()["detail"]


async def test_deleting_a_catalog_entry_does_not_destroy_the_plans(
    superuser_client: AsyncClient, session: AsyncSession
) -> None:
    """La FK est en `ON DELETE SET NULL` : retirer une recette ne doit pas supprimer les meubles."""
    from app.models import Element

    await superuser_client.post("/api/furniture-types", json=RECIPE)
    project = (await superuser_client.post("/api/projects", json={"name": "Meublé"})).json()
    room = (
        await superuser_client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Cuisine", "polygon": [[0, 0], [300, 0], [300, 300], [0, 300]]},
        )
    ).json()
    furniture_type_id = (
        await superuser_client.get("/api/furniture-types/tabouret")
    ).json()["id"]
    element = (
        await superuser_client.post(
            f"/api/faces/{room['faces'][0]['id']}/elements",
            json={"kind": "furniture", "furniture_type_id": furniture_type_id},
        )
    ).json()

    assert (await superuser_client.delete("/api/furniture-types/tabouret")).status_code == 204

    session.expunge_all()
    survivor = (
        await session.execute(select(Element).where(Element.id == element["id"]))
    ).scalar_one_or_none()
    assert survivor is not None, "supprimer une recette ne doit pas supprimer les meubles posés"
    assert survivor.furniture_type_id is None
