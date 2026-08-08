"""Mobilier non adossé et fond de plan — amendements A4 et A5 de `docs/spec-complete.md` §10.

`Element.face_id` était obligatoire : tout meuble était, par construction, collé à une face. Un
lit, une table, un îlot de cuisine n'étaient pas seulement mal placés, ils étaient **impossibles**.
La limitation venait du modèle, donc aucun travail sur l'éditeur 2D ne pouvait la lever.

Ce fichier vérifie les trois niveaux auxquels la règle doit tenir, et pas seulement le dernier :

1. **en base** — `CheckConstraint`, parce que SQLAdmin, la CLI, Celery et `psql` écrivent sans
   passer par Pydantic et que les `Field(...)` de SQLModel sont inertes sur `table=True` ;
2. **dans la géométrie** — fixture 11, calculée à la main, qui fige le placement et l'ordre ;
3. **dans l'API** — un meuble qui déborde du polygone est refusé avec un message qui dit où.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.geometry.scene import build_scene_graph
from app.models.base import ElementKind, FaceKind
from app.models.plan import Element, Face, Room
from app.models.user import User
from app.services.faces import (
    distance_to_polygon,
    element_fits_in_room,
    free_element_footprint,
    point_in_polygon,
)
from app.services.seed import seed_catalog
from tests.conftest import personal_organization
from tests.geometry.test_scene_graph import assert_matches

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]
# Pièce en L de la fixture 06 : le renfoncement est dans la boîte englobante et hors de la pièce.
FORME_EN_L: list[list[float]] = [
    [0, 0], [600, 0], [600, 200], [200, 200], [200, 500], [0, 500]
]

FIXTURE = Path(__file__).parent / "geometry" / "fixtures" / "11_mobilier_libre.json"


def _fixture() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def _catalog(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Le catalogue de la fixture, réindexé par entier : JSON n'a que des clés textuelles."""
    return {int(key): value for key, value in fixture["input"]["furniture_types"].items()}


def _element(**overrides: Any) -> Element:
    """Un meuble libre détaché de toute session, pour exercer la géométrie seule."""
    values: dict[str, Any] = {
        "kind": ElementKind.FURNITURE,
        "pos_x_cm": 200.0,
        "pos_y_cm": 150.0,
        "width_cm": 100.0,
        "height_cm": 80.0,
        "depth_cm": 60.0,
        "rotation_deg": 0.0,
    }
    return Element(**(values | overrides))


def _room(polygon: list[list[float]] | None = None, **overrides: Any) -> Room:
    values: dict[str, Any] = {
        "name": "Pièce",
        "polygon": polygon if polygon is not None else CARRE,
        "wall_thickness_cm": 10.0,
        "ceiling_height_cm": 250.0,
    }
    return Room(**(values | overrides))


# --- Géométrie : la fixture fait foi -------------------------------------------------------------


def test_free_furniture_matches_its_reference_fixture() -> None:
    """Calculée à la main avant l'implémentation (`CLAUDE.md`) : en cas d'écart, c'est le code."""
    fixture = _fixture()

    scene = build_scene_graph(fixture["input"], _catalog(fixture))

    assert_matches(scene["rooms"][0]["nodes"], fixture["expected_nodes"], "noeuds")


def test_the_room_areas_of_the_fixture_are_unchanged_by_free_furniture() -> None:
    fixture = _fixture()

    room = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]

    assert_matches(
        {key: room[key] for key in fixture["expected_room"]}, fixture["expected_room"], "piece"
    )


def test_free_furniture_is_emitted_after_everything_anchored_to_a_face() -> None:
    """L'ordre du JSON doit être stable d'un appel à l'autre : c'est ce qui fait le cache (P10)."""
    fixture = _fixture()

    nodes = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["nodes"]

    assert [node["face_label"] for node in nodes] == ["A", "A", None, None]
    first = json.dumps(build_scene_graph(fixture["input"], _catalog(fixture)), sort_keys=True)
    second = json.dumps(build_scene_graph(fixture["input"], _catalog(fixture)), sort_keys=True)
    assert first == second


def test_a_free_piece_of_furniture_rests_on_the_floor() -> None:
    """Son centre est à mi-hauteur : la boîte englobante repose sur y = 0, elle ne flotte pas."""
    fixture = _fixture()

    nodes = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["nodes"]
    free = [node for node in nodes if node["face_label"] is None]

    for node in free:
        assert node["position"][1] == pytest.approx(node["size_cm"][1] / 2.0)


def test_the_free_anchor_uses_the_plan_frame_and_not_the_bounding_box_corner() -> None:
    """La convention du sol-en-tant-que-face poserait le meuble ailleurs.

    Un élément posé sur la face `SOL` compte ses décalages depuis le coin de la boîte englobante ;
    un meuble libre donne son centre en coordonnées du plan. Sur une pièce éloignée de l'origine,
    confondre les deux déplace le meuble de la distance qui sépare la pièce de l'origine.
    """
    scene = build_scene_graph(
        {
            "project_id": 12,
            "rooms": [
                {
                    "id": 120,
                    "name": "Pièce éloignée",
                    "wall_thickness_cm": 10.0,
                    "ceiling_height_cm": 250.0,
                    "polygon": [[500, 500], [900, 500], [900, 800], [500, 800]],
                    "faces": [],
                    "elements": [
                        {
                            "id": 1200, "kind": "furniture",
                            "x_offset_cm": 0, "y_offset_cm": 0,
                            "pos_x_cm": 700, "pos_y_cm": 650,
                            "width_cm": 80, "height_cm": 40, "depth_cm": 40,
                            "rotation_deg": 0, "furniture_type_id": 1,
                            "colors": {}, "variant_params": {},
                        }
                    ],
                }
            ],
        },
        {
            1: {
                "id": 1, "slug": "table",
                "parts": [{"type": "box", "rel_position": [0.5, 0.5, 0.5],
                           "rel_size": [1, 1, 1], "color_slot": "plateau"}],
                "color_slots": ["plateau"],
            }
        },
    )

    node = scene["rooms"][0]["nodes"][0]
    assert node["position"] == [700.0, 20.0, 650.0]


def test_a_free_element_without_its_recipe_is_skipped_like_any_other() -> None:
    fixture = _fixture()

    scene = build_scene_graph(fixture["input"], {})

    assert [node["kind"] for node in scene["rooms"][0]["nodes"]] == ["wall"]


# --- Emprise au sol ------------------------------------------------------------------------------


def test_the_footprint_of_the_fixture_matches_its_hand_computed_corners() -> None:
    fixture = _fixture()
    expected = fixture["expected_footprints"]
    elements = fixture["input"]["rooms"][0]["elements"]

    for raw in elements:
        corners = free_element_footprint(
            _element(
                pos_x_cm=raw["pos_x_cm"],
                pos_y_cm=raw["pos_y_cm"],
                width_cm=raw["width_cm"],
                depth_cm=raw["depth_cm"],
                rotation_deg=raw["rotation_deg"],
            )
        )
        for index, corner in enumerate(corners):
            assert corner == pytest.approx(expected[str(raw["id"])][index], abs=1e-6), (
                f"emprise de l'élément {raw['id']}, coin {index}"
            )


def test_a_quarter_turn_swaps_width_and_depth_on_the_plan() -> None:
    """Sans conversion, une table de 200 sur 80 serait contrôlée dans le mauvais sens."""
    droite = free_element_footprint(_element(width_cm=200.0, depth_cm=80.0, rotation_deg=0.0))
    tournee = free_element_footprint(_element(width_cm=200.0, depth_cm=80.0, rotation_deg=90.0))

    def span(corners: list[tuple[float, float]], axis: int) -> float:
        return max(corner[axis] for corner in corners) - min(corner[axis] for corner in corners)

    assert (span(droite, 0), span(droite, 1)) == pytest.approx((200.0, 80.0))
    assert (span(tournee, 0), span(tournee, 1)) == pytest.approx((80.0, 200.0))


def test_the_footprint_stays_centred_on_the_declared_position() -> None:
    """Le centre et non un coin : tourner autour d'un coin déplacerait le meuble."""
    for angle in (0.0, 30.0, 90.0, -145.0, 360.0):
        corners = free_element_footprint(_element(rotation_deg=angle))
        assert sum(corner[0] for corner in corners) / 4.0 == pytest.approx(200.0)
        assert sum(corner[1] for corner in corners) / 4.0 == pytest.approx(150.0)


def test_point_in_polygon_tells_the_notch_of_an_l_from_the_room() -> None:
    """Une boîte englobante suffirait sur un rectangle et mentirait ici."""
    assert point_in_polygon(FORME_EN_L, 100.0, 100.0) is True
    assert point_in_polygon(FORME_EN_L, 400.0, 400.0) is False
    assert point_in_polygon(FORME_EN_L, 400.0, 100.0) is True


def test_the_distance_to_the_contour_is_measured_to_the_nearest_side() -> None:
    assert distance_to_polygon(CARRE, 410.0, 150.0) == pytest.approx(10.0)
    assert distance_to_polygon(CARRE, -25.0, 150.0) == pytest.approx(25.0)
    # Hors d'un coin : c'est la distance au sommet qui compte, pas celle à une droite.
    assert distance_to_polygon(CARRE, 403.0, 304.0) == pytest.approx(5.0)


# --- Validation d'encombrement -------------------------------------------------------------------


def test_a_bed_fitting_inside_the_room_is_accepted() -> None:
    assert element_fits_in_room(
        _element(width_cm=140.0, depth_cm=200.0, height_cm=45.0), _room()
    ) is None


def test_a_piece_of_furniture_pushed_flat_against_the_wall_is_accepted() -> None:
    """Le geste le plus courant du métier : le contour saisi est la limite que l'artisan voit."""
    contre_le_mur = _element(pos_x_cm=60.0, pos_y_cm=25.0, width_cm=120.0, depth_cm=50.0)

    assert element_fits_in_room(contre_le_mur, _room()) is None


def test_a_centred_but_oversized_piece_of_furniture_is_refused_with_its_overflow() -> None:
    """Le contrôle porte sur l'emprise : un centre dans la pièce ne dit rien des deux mètres."""
    trop_profond = _element(pos_x_cm=200.0, pos_y_cm=150.0, width_cm=100.0, depth_cm=400.0)

    problem = element_fits_in_room(trop_profond, _room())

    assert problem is not None
    assert "sort du contour" in problem
    # Débordement de (400 - 300) / 2 = 50 cm de chaque côté.
    assert "50.0 cm" in problem
    assert "(150.0, -50.0)" in problem or "(250.0, -50.0)" in problem


def test_the_refusal_accounts_for_the_rotation() -> None:
    """Une table de 380 sur 80 tient en largeur et non en profondeur : la tourner la fait sortir."""
    room = _room()
    table = _element(pos_x_cm=200.0, pos_y_cm=150.0, width_cm=380.0, depth_cm=80.0)

    assert element_fits_in_room(table, room) is None

    table.rotation_deg = 90.0
    problem = element_fits_in_room(table, room)
    assert problem is not None and "sort du contour" in problem


def test_a_piece_of_furniture_lying_across_the_notch_of_an_l_is_refused() -> None:
    """Les quatre coins peuvent être dans la pièce alors que l'emprise en sort quand même.

    Sur cette pièce en L, une table posée en travers du sommet rentrant a ses coins de part et
    d'autre du retour et son milieu dans le renfoncement — donc dehors. Le contrôle des seuls
    coins la laisserait passer.
    """
    room = _room(FORME_EN_L)
    en_travers = _element(
        pos_x_cm=200.0, pos_y_cm=200.0, width_cm=300.0, depth_cm=20.0, rotation_deg=45.0
    )

    problem = element_fits_in_room(en_travers, room)

    assert problem is not None
    assert "traverse le contour" in problem or "sort du contour" in problem


def test_furniture_taller_than_the_room_is_refused() -> None:
    problem = element_fits_in_room(_element(height_cm=300.0), _room())

    assert problem is not None and "plus haut que la pièce" in problem


def test_an_opening_is_never_accepted_on_the_floor() -> None:
    problem = element_fits_in_room(_element(kind=ElementKind.WINDOW), _room())

    assert problem is not None and "percement" in problem


def test_a_room_without_a_polygon_bounds_nothing() -> None:
    """Une pièce esquissée n'a pas de contour : il n'y a rien à contrôler, pas de refus muet."""
    assert element_fits_in_room(_element(), _room([])) is None


# --- Contraintes de base -------------------------------------------------------------------------


async def _persisted_room(session: AsyncSession, owner: User) -> Room:
    from app.models.plan import Project

    organization = await personal_organization(session, owner)
    project = Project(
        name="Ancrages", owner_id=owner.id or 0, organization_id=organization.id or 0
    )
    session.add(project)
    await session.flush()
    room = Room(project_id=project.id or 0, name="Chambre", polygon=CARRE)
    session.add(room)
    await session.flush()
    face = Face(room_id=room.id or 0, label="A", kind=FaceKind.WALL)
    session.add(face)
    await session.commit()
    await session.refresh(room)
    return room


async def _face_of(session: AsyncSession, room: Room) -> Face:
    from sqlmodel import col, select

    return (
        await session.execute(select(Face).where(col(Face.room_id) == room.id))
    ).scalar_one()


@pytest.mark.parametrize(
    ("description", "values"),
    [
        ("les deux ancrages", {"anchor": "both"}),
        ("aucun ancrage", {"anchor": "none"}),
        ("pièce sans position", {"anchor": "room", "pos_x_cm": None, "pos_y_cm": None}),
        ("face avec position", {"anchor": "face", "pos_x_cm": 10.0, "pos_y_cm": 10.0}),
    ],
)
async def test_the_database_refuses_anything_but_exactly_one_anchor(
    session: AsyncSession, owner: User, description: str, values: dict[str, Any]
) -> None:
    """La contrainte est en base et pas seulement dans Pydantic.

    C'est la leçon de la vague 1 : `Room(wall_thickness_cm=-5)` était accepté parce que les
    `Field(gt=0)` de SQLModel sont inertes sur les modèles `table=True`. SQLAdmin, la CLI, Celery
    et `psql` écrivent tous sans passer par l'API.
    """
    room = await _persisted_room(session, owner)
    face = await _face_of(session, room)
    anchor = values.pop("anchor")

    element = Element(
        face_id=face.id if anchor in ("face", "both") else None,
        room_id=room.id if anchor in ("room", "both") else None,
        pos_x_cm=values.get("pos_x_cm", 100.0 if anchor in ("room", "both") else None),
        pos_y_cm=values.get("pos_y_cm", 100.0 if anchor in ("room", "both") else None),
    )
    session.add(element)

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_the_database_refuses_an_opening_without_a_face(
    session: AsyncSession, owner: User
) -> None:
    room = await _persisted_room(session, owner)
    session.add(
        Element(
            room_id=room.id, pos_x_cm=100.0, pos_y_cm=100.0, kind=ElementKind.WINDOW
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_free_element_is_accepted_by_the_database(
    session: AsyncSession, owner: User
) -> None:
    """Le cas nominal : sans lui, les refus ci-dessus prouveraient seulement que rien ne passe."""
    room = await _persisted_room(session, owner)
    session.add(Element(room_id=room.id, pos_x_cm=200.0, pos_y_cm=150.0))

    await session.commit()


# --- API -----------------------------------------------------------------------------------------


async def _plan(client: AsyncClient, polygon: list[list[float]] | None = None) -> dict[str, Any]:
    project = (await client.post("/api/projects", json={"name": "Aménagement"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Séjour", "polygon": polygon or CARRE},
        )
    ).json()
    return {"project": project, "room": room}


async def test_a_bed_can_finally_be_placed_in_the_middle_of_the_room(
    auth_client: AsyncClient,
) -> None:
    """Le cœur du lot : ce geste était littéralement impossible avant l'amendement A4."""
    plan = await _plan(auth_client)

    created = await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={
            "kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150,
            "width_cm": 140, "height_cm": 45, "depth_cm": 200,
        },
    )

    assert created.status_code == 201, created.text
    element = created.json()
    assert element["face_id"] is None
    assert element["room_id"] == plan["room"]["id"]
    assert (element["pos_x_cm"], element["pos_y_cm"]) == (200.0, 150.0)


async def test_the_room_lists_its_free_furniture_apart_from_its_faces(
    auth_client: AsyncClient,
) -> None:
    plan = await _plan(auth_client)
    await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150},
    )

    room = (await auth_client.get(f"/api/rooms/{plan['room']['id']}")).json()

    assert [element["pos_x_cm"] for element in room["free_elements"]] == [200.0]
    assert all(face["elements"] == [] for face in room["faces"])

    project = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()
    assert len(project["rooms"][0]["free_elements"]) == 1


async def test_free_furniture_reaches_the_3d_scene(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Sans cela, le modèle serait corrigé et le produit ne montrerait toujours rien."""
    await seed_catalog(session)
    lit_id = (await auth_client.get("/api/furniture-types/lit")).json()["id"]
    plan = await _plan(auth_client)
    await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={
            "kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150,
            "width_cm": 140, "height_cm": 45, "depth_cm": 200,
            "furniture_type_id": lit_id,
        },
    )

    scene = (await auth_client.get(f"/api/projects/{plan['project']['id']}/scene")).json()
    free = [
        node
        for node in scene["rooms"][0]["nodes"]
        if node["kind"] == "furniture" and node["face_label"] is None
    ]

    assert len(free) == 1
    assert free[0]["position"] == [200.0, 22.5, 150.0]


async def test_an_opening_cannot_be_anchored_to_a_room(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)

    refused = await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={"kind": "window", "pos_x_cm": 200, "pos_y_cm": 150},
    )

    assert refused.status_code == 422, refused.text
    assert "mur" in refused.text


async def test_furniture_overflowing_the_polygon_is_refused_with_where_and_by_how_much(
    auth_client: AsyncClient,
) -> None:
    plan = await _plan(auth_client)

    refused = await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={
            "kind": "furniture", "pos_x_cm": 380, "pos_y_cm": 150,
            "width_cm": 100, "height_cm": 80, "depth_cm": 60,
        },
    )

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert "sort du contour" in detail
    assert "(430.0, " in detail  # le coin fautif
    assert "30.0 cm" in detail  # de combien


async def test_a_free_element_moves_with_a_patch(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150},
        )
    ).json()

    moved = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"pos_x_cm": 120, "pos_y_cm": 90}
    )

    assert moved.status_code == 200, moved.text
    assert (moved.json()["pos_x_cm"], moved.json()["pos_y_cm"]) == (120.0, 90.0)
    assert (await auth_client.delete(f"/api/elements/{element['id']}")).status_code == 204


async def test_a_patch_never_writes_a_placement_in_the_other_frame(
    auth_client: AsyncClient,
) -> None:
    """Changer d'ancrage n'est pas une modification (spec §10, A4) : c'est un 422, pas une 500."""
    plan = await _plan(auth_client)
    libre = (
        await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150},
        )
    ).json()
    adosse = (
        await auth_client.post(
            f"/api/faces/{plan['room']['faces'][0]['id']}/elements",
            json={"kind": "furniture", "x_offset_cm": 10, "width_cm": 50, "height_cm": 50},
        )
    ).json()

    on_the_free_one = await auth_client.patch(
        f"/api/elements/{libre['id']}", json={"x_offset_cm": 42}
    )
    on_the_anchored_one = await auth_client.patch(
        f"/api/elements/{adosse['id']}", json={"pos_x_cm": 42}
    )

    assert on_the_free_one.status_code == 422, on_the_free_one.text
    assert on_the_anchored_one.status_code == 422, on_the_anchored_one.text
    assert "autre repère" in on_the_anchored_one.json()["detail"]


async def test_free_furniture_survives_the_wall_it_was_never_attached_to(
    auth_client: AsyncClient,
) -> None:
    """C'est tout l'intérêt de l'ancrage à la pièce.

    Redessiner le contour supprime des murs et, avec eux, ce qui y était posé. Un lit n'a aucune
    raison de disparaître parce qu'on a rectifié la forme du séjour.
    """
    plan = await _plan(auth_client)
    lit = (
        await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json={
                "kind": "furniture", "pos_x_cm": 150, "pos_y_cm": 120,
                "width_cm": 140, "height_cm": 45, "depth_cm": 200,
            },
        )
    ).json()

    reshaped = await auth_client.patch(
        f"/api/rooms/{plan['room']['id']}",
        json={"polygon": [[0, 0], [300, 0], [300, 250], [0, 250]]},
    )
    assert reshaped.status_code == 200, reshaped.text

    survivor = next(
        element for element in reshaped.json()["free_elements"] if element["id"] == lit["id"]
    )
    # Ramené dans le nouveau contour plutôt que supprimé : demi-emprise 70 x 100, donc le centre
    # ne peut pas descendre sous (70, 100) ni dépasser (230, 150).
    assert 70.0 <= survivor["pos_x_cm"] <= 230.0
    assert 100.0 <= survivor["pos_y_cm"] <= 150.0


async def test_a_free_element_is_deleted_with_its_room(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150},
        )
    ).json()

    assert (await auth_client.delete(f"/api/rooms/{plan['room']['id']}")).status_code == 204
    assert (await auth_client.patch(
        f"/api/elements/{element['id']}", json={"pos_x_cm": 10}
    )).status_code == 404


async def test_placing_free_furniture_bumps_the_project_version(
    auth_client: AsyncClient,
) -> None:
    """Toute écriture passe par `_claim_project` — la nouvelle route ne fait pas exception."""
    plan = await _plan(auth_client)
    before = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]

    stale = await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150, "version": before - 1},
    )
    assert stale.status_code == 409, stale.text

    await auth_client.post(
        f"/api/rooms/{plan['room']['id']}/elements",
        json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150, "version": before},
    )
    after = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]
    assert after == before + 1


async def test_the_back_office_finds_the_project_of_a_free_element(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """SQLAdmin écrit sans passer par l'API, donc sans incrémenter `Project.version`.

    C'est `purge_scene_cache_for` qui rattrape ce trou, et elle a besoin du projet. La remontée
    passait par `Element.face_id` seul : nul sur un meuble libre, elle rendait `None` **sans
    lever**, et la scène périmée continuait d'être servie après correction en administration. Le
    silence est ce qui rend ce défaut coûteux — les deux ancrages sont donc vérifiés ici.
    """
    from app.admin import _project_id_of

    plan = await _plan(auth_client)
    face_id = plan["room"]["faces"][0]["id"]
    libre = (
        await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json={"kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150},
        )
    ).json()
    adosse = (
        await auth_client.post(
            f"/api/faces/{face_id}/elements",
            json={"kind": "furniture", "x_offset_cm": 10, "y_offset_cm": 0},
        )
    ).json()
    await session.commit()

    # `_project_id_of` lit par le moteur **synchrone** du back-office : seules les lignes
    # committées lui sont visibles, d'où le `commit` ci-dessus.
    assert _project_id_of(await session.get(Element, libre["id"])) == plan["project"]["id"]
    assert _project_id_of(await session.get(Element, adosse["id"])) == plan["project"]["id"]


# --- Fond de plan (amendement A5) ----------------------------------------------------------------


async def test_a_room_carries_the_calibration_of_its_background(
    auth_client: AsyncClient,
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Relevé"})).json()

    created = await auth_client.post(
        f"/api/projects/{project['id']}/rooms",
        json={
            "name": "Séjour",
            "polygon": CARRE,
            "background_url": "/media/plans/architecte.png",
            "background_scale_cm_per_px": 2.5,
            "background_offset_x_cm": -40,
            "background_offset_y_cm": 12.5,
            "background_rotation_deg": -1.5,
            "background_opacity": 0.35,
        },
    )

    assert created.status_code == 201, created.text
    room = created.json()
    assert room["background_url"] == "/media/plans/architecte.png"
    assert room["background_scale_cm_per_px"] == 2.5
    assert room["background_offset_x_cm"] == -40.0
    assert room["background_opacity"] == 0.35


async def test_an_uncalibrated_background_keeps_a_null_scale(auth_client: AsyncClient) -> None:
    """« Image posée, pas encore calibrée » est un état réel : une échelle inventée mentirait."""
    project = (await auth_client.post("/api/projects", json={"name": "Relevé"})).json()

    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Séjour", "background_url": "/media/photo.jpg"},
        )
    ).json()

    assert room["background_scale_cm_per_px"] is None
    assert room["background_opacity"] == 1.0


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//exemple-malveillant.fr/plan.png",
        "/\\exemple-malveillant.fr/plan.png",
        "http://exemple.fr/plan.png",
        "/media/mon plan.png",
    ],
)
async def test_a_dangerous_background_url_is_refused(auth_client: AsyncClient, url: str) -> None:
    """Entrée utilisateur relue dans un attribut d'image : OWASP A03, validée à l'écriture."""
    project = (await auth_client.post("/api/projects", json={"name": "Relevé"})).json()

    refused = await auth_client.post(
        f"/api/projects/{project['id']}/rooms",
        json={"name": "Séjour", "background_url": url},
    )

    assert refused.status_code == 422, refused.text


async def test_the_background_can_be_calibrated_then_removed(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Relevé"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Séjour", "polygon": CARRE, "background_url": "/media/plan.png"},
        )
    ).json()

    calibrated = await auth_client.patch(
        f"/api/rooms/{room['id']}", json={"background_scale_cm_per_px": 1.75}
    )
    assert calibrated.status_code == 200, calibrated.text
    assert calibrated.json()["background_scale_cm_per_px"] == 1.75

    cleared = await auth_client.patch(
        f"/api/rooms/{room['id']}",
        json={"background_url": None, "background_scale_cm_per_px": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["background_url"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"background_opacity": 1.5},
        {"background_opacity": -0.1},
        {"background_scale_cm_per_px": 0},
        {"background_scale_cm_per_px": -3},
        {"background_rotation_deg": 400},
    ],
)
async def test_an_out_of_range_calibration_is_refused(
    auth_client: AsyncClient, payload: dict[str, Any]
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Relevé"})).json()

    refused = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Séjour"} | payload
    )

    assert refused.status_code == 422, refused.text


async def test_the_database_refuses_an_out_of_range_calibration(
    session: AsyncSession, owner: User
) -> None:
    """Même leçon que pour les ancrages : Pydantic ne protège pas SQLAdmin ni `psql`."""
    room = await _persisted_room(session, owner)
    room.background_opacity = 2.0

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


def test_the_module_uses_the_same_rotation_convention_as_the_scene_graph() -> None:
    """Contre-mesure : deux conventions de rotation feraient diverger contrôle et rendu.

    Le contrôle d'encombrement vit dans `services/faces.py` et le rendu dans `geometry/scene.py`.
    Rien ne les relie mécaniquement — ce test les confronte sur le même meuble.
    """
    fixture = _fixture()
    table = fixture["input"]["rooms"][0]["elements"][1]

    node = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["nodes"][3]
    corners = free_element_footprint(
        _element(
            pos_x_cm=table["pos_x_cm"],
            pos_y_cm=table["pos_y_cm"],
            width_cm=table["width_cm"],
            depth_cm=table["depth_cm"],
            rotation_deg=table["rotation_deg"],
        )
    )

    # Le nœud tourne de `rotation_y` autour de +Y ; l'emprise doit tourner d'autant dans le plan.
    angle = node["rotation_y"]
    half_width, half_depth = table["width_cm"] / 2.0, table["depth_cm"] / 2.0
    expected = (
        table["pos_x_cm"] + half_width * math.cos(angle) + half_depth * math.sin(angle),
        table["pos_y_cm"] - half_width * math.sin(angle) + half_depth * math.cos(angle),
    )
    assert corners[2] == pytest.approx(expected, abs=1e-6)
