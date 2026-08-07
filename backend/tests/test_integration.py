"""Ticket P11 — tests d'intégration et cas limites (`docs/plan-generation-ia.md` §5).

Les tests des tickets précédents vérifient chacun une couche. Ceux-ci suivent des **parcours
complets**, de l'inscription à l'export, et poussent le système dans ses coins : c'est là que se
logent les défauts d'assemblage, invisibles quand chaque brique est testée isolément.
"""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.plan import Element, Face, Project, Room, SharedView
from app.services.seed import seed_catalog

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]
EN_L: list[list[float]] = [[0, 0], [600, 0], [600, 400], [300, 400], [300, 250], [0, 250]]


# --- Parcours complet -------------------------------------------------------------------------


async def test_a_full_journey_from_signup_to_export(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Le parcours nominal complet, dans l'ordre où un utilisateur le vit."""
    await seed_catalog(session)

    # 1. Inscription puis connexion.
    assert (
        await client.post(
            "/api/auth/register",
            json={"email": "parcours@exemple.fr", "password": "motdepasse-parcours-2026"},
        )
    ).status_code == 202
    tokens = await client.post(
        "/api/auth/token",
        data={"username": "parcours@exemple.fr", "password": "motdepasse-parcours-2026"},
    )
    client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"

    # 2. Création du projet et d'une pièce en L.
    project = (await client.post("/api/projects", json={"name": "Studio"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Pièce à vivre", "polygon": EN_L, "wall_thickness_cm": 12},
        )
    ).json()
    assert len([f for f in room["faces"] if f["kind"] == "wall"]) == 6

    # 3. Revêtement, ouverture et meuble.
    face_a = next(f for f in room["faces"] if f["label"] == "A")
    await client.patch(
        f"/api/faces/{face_a['id']}",
        json={"covering": {"color": "#f0e6d2", "material": "peinture", "pattern": "straight"}},
    )
    await client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 100, "y_offset_cm": 90,
              "width_cm": 120, "height_cm": 110},
    )
    commode = (await client.get("/api/furniture-types/commode")).json()
    await client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": commode["id"],
              "x_offset_cm": 300, "y_offset_cm": 0,
              "width_cm": 100, "height_cm": 85, "depth_cm": 45,
              "colors": {"corps": "#8b5a2b"}},
    )

    # 4. Scène 3D.
    scene = (await client.get(f"/api/projects/{project['id']}/scene")).json()
    room_scene = scene["rooms"][0]
    walls = [n for n in room_scene["nodes"] if n["kind"] == "wall"]
    furniture = [n for n in room_scene["nodes"] if n["kind"] == "furniture"]
    assert len(walls) == 6
    assert len(furniture) == 1
    assert len(furniture[0]["primitives"]) == 9
    wall_a = next(n for n in walls if n["face_label"] == "A")
    assert len(wall_a["holes"]) == 1, "la fenêtre doit être un trou, le meuble non"
    assert len(room_scene["cameras"]) == 9  # 3 vues d'ensemble + 6 murs

    # 5. Partage public.
    shared = (
        await client.post(
            f"/api/projects/{project['id']}/shared-views",
            json={"state": {"camera_preset": "face-A", "visible_faces": ["A"]}},
        )
    ).json()
    public = await AsyncClient(transport=client._transport, base_url="http://test").get(
        f"/api/public/views/{shared['token']}"
    )
    assert public.status_code == 200
    # Le lien public ne porte pas le nom du projet tant que le propriétaire n'a pas choisi un
    # libellé public : « Studio » ici, mais « Rénovation Dupont, 12 rue des Lilas » en vrai.
    assert public.json()["project_name"] == "Vue partagée"
    assert "Studio" not in public.text

    # 6. Export PDF.
    export = (await client.post(f"/api/projects/{project['id']}/exports/pdf")).json()
    status_body = (await client.get(export["poll_url"])).json()
    assert status_body["result"]["size_bytes"] > 0


# --- Cas limites géométriques ---------------------------------------------------------------


async def test_a_room_with_many_walls_is_lettered_beyond_z(auth_client: AsyncClient) -> None:
    """Une pièce très découpée doit rester correctement lettrée au-delà de 26 murs."""
    project = (await auth_client.post("/api/projects", json={"name": "Dentelle"})).json()
    # Un polygone en escalier de 30 sommets.
    polygon: list[list[float]] = []
    for index in range(15):
        polygon.append([index * 40.0, 0.0])
        polygon.append([index * 40.0, 40.0])
    polygon.append([600.0, 400.0])
    polygon.append([0.0, 400.0])

    room = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Escalier", "polygon": polygon}
    )

    assert room.status_code == 201, room.text
    walls = [f for f in room.json()["faces"] if f["kind"] == "wall"]
    labels = [f["label"] for f in walls]
    assert len(labels) == len(set(labels)), "des étiquettes de mur sont dupliquées"
    assert "AA" in labels, "le lettrage doit dépasser Z sans repartir à A"


async def test_a_room_far_from_the_origin_behaves_like_one_at_the_origin(
    auth_client: AsyncClient,
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Éloigné"})).json()
    decale = [[x + 5000, y + 8000] for x, y in CARRE]

    room = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Loin", "polygon": decale}
    )
    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    assert room.status_code == 201
    assert scene["rooms"][0]["floor_area_cm2"] == 120000.0
    for camera in scene["rooms"][0]["cameras"]:
        if camera["face_label"]:
            x, _y, z = camera["position"]
            assert 5000 <= x <= 5400, f"caméra hors de la pièce : {camera}"
            assert 8000 <= z <= 8300, f"caméra hors de la pièce : {camera}"


async def test_negative_coordinates_are_supported(auth_client: AsyncClient) -> None:
    """Rien n'oblige l'utilisateur à dessiner dans le quadrant positif."""
    project = (await auth_client.post("/api/projects", json={"name": "Négatif"})).json()
    negatif = [[-400, -300], [0, -300], [0, 0], [-400, 0]]

    room = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Négative", "polygon": negatif}
    )
    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    assert room.status_code == 201
    assert scene["rooms"][0]["floor_area_cm2"] == 120000.0


async def test_the_maximum_polygon_size_is_enforced(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Trop"})).json()
    enorme = [[index * 10.0, (index % 2) * 10.0] for index in range(80)]

    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Trop", "polygon": enorme}
    )
    assert response.status_code == 422


# --- Cycles de vie et cascades -----------------------------------------------------------------


async def test_deleting_a_user_removes_everything_they_own(
    client: AsyncClient, session: AsyncSession
) -> None:
    """RGPD : le droit à l'effacement suppose une suppression en cascade complète."""
    from app.models.user import User

    await client.post(
        "/api/auth/register",
        json={"email": "efface@exemple.fr", "password": "motdepasse-efface-2026"},
    )
    tokens = await client.post(
        "/api/auth/token",
        data={"username": "efface@exemple.fr", "password": "motdepasse-efface-2026"},
    )
    client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"

    project = (await client.post("/api/projects", json={"name": "À effacer"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "P", "polygon": CARRE}
        )
    ).json()
    await client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements", json={"kind": "window"}
    )
    await client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": {"camera_preset": "dessus"}},
    )

    user = (
        await session.execute(select(User).where(col(User.email) == "efface@exemple.fr"))
    ).scalar_one()
    await session.delete(user)
    await session.commit()

    # `SharedView` est inclus volontairement : c'est un jeton d'accès **public** encore vivant,
    # la ligne la plus sensible à laisser derrière soi.
    for model in (Project, Room, Face, Element, SharedView):
        remaining = (await session.execute(select(model))).scalars().all()
        assert remaining == [], f"{model.__name__} survit à la suppression du compte"


async def test_an_empty_project_is_valid_everywhere(auth_client: AsyncClient) -> None:
    """Un projet sans pièce ne doit casser aucune des couches en aval."""
    project = (await auth_client.post("/api/projects", json={"name": "Vide"})).json()

    assert (await auth_client.get(f"/api/projects/{project['id']}")).status_code == 200
    scene = await auth_client.get(f"/api/projects/{project['id']}/scene")
    assert scene.status_code == 200
    assert scene.json()["rooms"] == []
    export = await auth_client.get(f"/api/projects/{project['id']}/exports/pdf/direct")
    assert export.status_code == 200
    assert export.content.startswith(b"%PDF-")


async def test_a_room_without_polygon_survives_the_whole_pipeline(
    auth_client: AsyncClient,
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Esquisse"})).json()
    await auth_client.post(f"/api/projects/{project['id']}/rooms", json={"name": "À tracer"})

    scene = await auth_client.get(f"/api/projects/{project['id']}/scene")
    export = await auth_client.get(f"/api/projects/{project['id']}/exports/pdf/direct")

    assert scene.status_code == 200
    assert scene.json()["rooms"][0]["nodes"] == []
    assert export.status_code == 200


# --- Concurrence ----------------------------------------------------------------------------


async def test_concurrent_reads_are_consistent(auth_client: AsyncClient) -> None:
    """Dix lectures lancées ensemble doivent renvoyer exactement la même scène.

    Limite connue et assumée : la suite partage une seule `AsyncSession`, donc les requêtes se
    sérialisent côté base. Ce test vérifie donc le **déterminisme de la sortie**, pas la
    concurrence réelle d'accès à PostgreSQL — laquelle est couverte par le test de verrouillage
    optimiste, qui utilise bien deux sessions distinctes.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Concurrent"})).json()
    await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "P", "polygon": CARRE}
    )

    responses = await asyncio.gather(
        *[auth_client.get(f"/api/projects/{project['id']}/scene") for _ in range(10)]
    )

    bodies = {response.text for response in responses}
    assert all(response.status_code == 200 for response in responses)
    assert len(bodies) == 1, "des lectures simultanées renvoient des scènes différentes"


async def test_two_stale_writes_cannot_both_win(auth_client: AsyncClient) -> None:
    """Deux clients partis de la même version : le second doit être refusé, pas écrasant."""
    project = (await auth_client.post("/api/projects", json={"name": "Duel"})).json()
    version = project["version"]

    first = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Écriture A", "version": version}
    )
    second = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Écriture B", "version": version}
    )

    assert first.status_code == 200
    assert second.status_code == 409
    final = (await auth_client.get(f"/api/projects/{project['id']}")).json()
    assert final["name"] == "Écriture A"


# --- Cohérence entre couches -------------------------------------------------------------------


async def test_the_scene_always_matches_the_plan(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Chaque face du plan doit avoir exactement un nœud dans la scène, et réciproquement."""
    await seed_catalog(session)
    project = (await auth_client.post("/api/projects", json={"name": "Cohérence"})).json()
    for index, polygon in enumerate((CARRE, EN_L)):
        decale = [[x + index * 1000, y] for x, y in polygon]
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": f"Pièce {index}", "polygon": decale},
        )

    plan = (await auth_client.get(f"/api/projects/{project['id']}")).json()
    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    for plan_room, scene_room in zip(plan["rooms"], scene["rooms"], strict=True):
        plan_labels = sorted(face["label"] for face in plan_room["faces"])
        scene_labels = sorted(
            node["face_label"] for node in scene_room["nodes"] if node["kind"] != "furniture"
        )
        assert plan_labels == scene_labels, (
            f"pièce {plan_room['name']} : plan {plan_labels} ≠ scène {scene_labels}"
        )


@pytest.mark.parametrize("kind", ["door_hinged", "door_sliding", "window"])
async def test_every_opening_kind_becomes_a_hole(
    auth_client: AsyncClient, kind: str
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Ouvertures"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "P", "polygon": CARRE}
        )
    ).json()
    face_a = next(f for f in room["faces"] if f["label"] == "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": kind, "x_offset_cm": 50, "y_offset_cm": 0,
              "width_cm": 90, "height_cm": 200},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()
    wall_a = next(n for n in scene["rooms"][0]["nodes"] if n.get("face_label") == "A")

    assert len(wall_a["holes"]) == 1
    assert all(n["kind"] != "furniture" for n in scene["rooms"][0]["nodes"])
