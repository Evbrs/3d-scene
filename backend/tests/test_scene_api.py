"""Le scene graph exposé par l'API (ticket P6).

Les fixtures de `tests/geometry/` vérifient le *calcul* ; ici on vérifie le *chemin complet* :
depuis les modèles en base jusqu'au JSON, avec les permissions.
"""

import threading
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.seed import seed_catalog

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def _plan(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    project: dict[str, Any] = (
        await client.post("/api/projects", json={"name": "Scène"})
    ).json()
    room: dict[str, Any] = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Salon", "polygon": CARRE, "wall_thickness_cm": 10},
        )
    ).json()
    return project, room


async def test_the_scene_graph_of_an_empty_project_is_empty(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Vide"})).json()

    response = await auth_client.get(f"/api/projects/{project['id']}/scene")

    assert response.status_code == 200
    assert response.json() == {"units": "cm", "project_id": project["id"], "rooms": []}


async def test_the_scene_graph_mirrors_the_plan(auth_client: AsyncClient) -> None:
    project, _room = await _plan(auth_client)

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    assert scene["units"] == "cm"
    room = scene["rooms"][0]
    assert room["floor_area_cm2"] == 120000.0
    assert [node["face_label"] for node in room["nodes"]] == ["A", "B", "C", "D", "SOL", "PLAFOND"]
    assert len(room["cameras"]) == 7  # dessus + isométrique + orbite + 4 faces


async def test_an_opening_becomes_a_hole_and_not_an_object(auth_client: AsyncClient) -> None:
    project, room = await _plan(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 80, "y_offset_cm": 100,
              "width_cm": 90, "height_cm": 110},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    wall_a = next(node for node in scene["rooms"][0]["nodes"] if node["face_label"] == "A")
    assert wall_a["holes"] == [[[80.0, 100.0], [170.0, 100.0], [170.0, 210.0], [80.0, 210.0]]]
    assert all(node["kind"] != "furniture" for node in scene["rooms"][0]["nodes"])


async def test_a_furniture_element_is_expanded_from_the_catalog(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)
    commode_id = (await auth_client.get("/api/furniture-types/commode")).json()["id"]

    project, room = await _plan(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={
            "kind": "furniture", "furniture_type_id": commode_id,
            "x_offset_cm": 50, "y_offset_cm": 0,
            "width_cm": 100, "height_cm": 85, "depth_cm": 45,
            "colors": {"corps": "#8b5a2b"},
        },
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    furniture = [node for node in scene["rooms"][0]["nodes"] if node["kind"] == "furniture"]
    assert len(furniture) == 1
    assert furniture[0]["furniture_type_slug"] == "commode"
    assert furniture[0]["position"] == [100.0, 42.5, 27.5]
    # 1 corps + 4 façades + 4 poignées : la répétition est développée côté serveur.
    assert len(furniture[0]["primitives"]) == 9
    assert furniture[0]["requires_csg"] is False


async def test_a_bathtub_is_flagged_as_needing_csg(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Spec §4.2 : le viewer n'active `three-bvh-csg` que sur les meubles qui l'exigent."""
    await seed_catalog(session)
    baignoire_id = (await auth_client.get("/api/furniture-types/baignoire")).json()["id"]

    project, room = await _plan(auth_client)
    await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": baignoire_id,
              "width_cm": 170, "height_cm": 55, "depth_cm": 75},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()
    furniture = next(n for n in scene["rooms"][0]["nodes"] if n["kind"] == "furniture")

    assert furniture["requires_csg"] is True


async def test_the_scene_graph_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/projects/1/scene")).status_code == 401


async def test_another_account_cannot_read_the_scene(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project, _room = await _plan(auth_client)

    assert (await other_client.get(f"/api/projects/{project['id']}/scene")).status_code == 404


async def test_the_scene_graph_is_never_built_on_the_event_loop(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le calcul est du numpy pur, donc bloquant.

    Sur la boucle d'événements il gèle *toutes* les requêtes en cours le temps du calcul — et le
    même point d'entrée sert la lecture publique (P8), atteignable sans authentification.
    """
    from app.geometry import scene as geometry_scene

    project, _room = await _plan(auth_client)
    seen: list[str] = []
    original = geometry_scene.build_scene_graph

    def spy(plan: dict[str, Any], catalog: dict[int, dict[str, Any]]) -> dict[str, Any]:
        seen.append(threading.current_thread().name)
        return original(plan, catalog)

    monkeypatch.setattr("app.api.scene.build_scene_graph", spy)
    response = await auth_client.get(f"/api/projects/{project['id']}/scene")

    assert response.status_code == 200
    assert seen and threading.main_thread().name not in seen, (
        f"le scene graph est calculé sur {seen}, donc sur la boucle d'événements"
    )


async def test_the_scene_graph_is_stable_between_two_calls(auth_client: AsyncClient) -> None:
    """Nécessaire au cache de P10 : deux appels identiques doivent donner le même JSON."""
    project, _room = await _plan(auth_client)

    first = (await auth_client.get(f"/api/projects/{project['id']}/scene")).text
    second = (await auth_client.get(f"/api/projects/{project['id']}/scene")).text

    assert first == second
