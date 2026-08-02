"""Critères d'acceptation du ticket P3 (API CRUD du plan 2D).

Référence : `docs/spec-complete.md` §7 (phase P3) et §8 (verrouillage optimiste, eager loading).
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import FurnitureType, Project
from app.services.faces import CEILING_LABEL, FLOOR_LABEL, wall_label

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


# --- Lettrage des faces (règle métier pure) ---------------------------------------------------


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA")],
)
def test_wall_labels_follow_the_alphabet_and_do_not_wrap(index: int, expected: str) -> None:
    """Au-delà de 26 murs, le lettrage doit continuer sans jamais réutiliser une lettre."""
    assert wall_label(index) == expected


def test_wall_labels_are_unique_over_a_large_polygon() -> None:
    labels = [wall_label(index) for index in range(64)]
    assert len(set(labels)) == len(labels)


def test_a_negative_wall_index_is_refused() -> None:
    with pytest.raises(ValueError):
        wall_label(-1)


# --- Projets ----------------------------------------------------------------------------------


async def test_create_read_update_delete_a_project(auth_client: AsyncClient) -> None:
    created = await auth_client.post(
        "/api/projects", json={"name": "Rénovation T3", "description": "Appartement 1970"}
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["name"] == "Rénovation T3"
    assert project["version"] == 1
    assert project["rooms"] == []

    read = await auth_client.get(f"/api/projects/{project['id']}")
    assert read.status_code == 200
    assert read.json()["id"] == project["id"]

    updated = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Rénovation T3 bis"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Rénovation T3 bis"

    deleted = await auth_client.delete(f"/api/projects/{project['id']}")
    assert deleted.status_code == 204
    assert (await auth_client.get(f"/api/projects/{project['id']}")).status_code == 404


async def test_the_project_list_is_paginated_and_scoped_to_the_owner(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    for index in range(3):
        await auth_client.post("/api/projects", json={"name": f"Projet {index}"})
    await other_client.post("/api/projects", json={"name": "Projet de quelqu'un d'autre"})

    page = await auth_client.get("/api/projects", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3, "la liste doit ignorer les projets des autres comptes"
    assert len(body["items"]) == 2
    assert all("Projet de quelqu'un" not in item["name"] for item in body["items"])


async def test_a_client_cannot_choose_the_owner_of_a_project(auth_client: AsyncClient) -> None:
    """Champs interdits : `extra="forbid"` évite l'assignation en masse (OWASP A08)."""
    response = await auth_client.post(
        "/api/projects", json={"name": "Piraté", "owner_id": 999, "id": 42}
    )
    assert response.status_code == 422


async def test_an_empty_project_name_is_refused(auth_client: AsyncClient) -> None:
    assert (await auth_client.post("/api/projects", json={"name": ""})).status_code == 422


# --- Verrouillage optimiste (spec §8, cas 3) ---------------------------------------------------


async def test_a_stale_version_is_rejected_with_409(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Concurrent"})).json()

    first = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Écriture A", "version": 1}
    )
    assert first.status_code == 200
    assert first.json()["version"] == 2, "la version doit être incrémentée à chaque écriture"

    # Deuxième client encore sur la version 1 : son écriture doit être refusée, pas écrasante.
    second = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Écriture B", "version": 1}
    )
    assert second.status_code == 409
    assert second.headers["X-Current-Version"] == "2"

    unchanged = await auth_client.get(f"/api/projects/{project['id']}")
    assert unchanged.json()["name"] == "Écriture A", "l'écriture perdante ne doit rien écraser"


async def test_an_update_without_version_still_works(auth_client: AsyncClient) -> None:
    """Le verrouillage est opt-in : un client qui ne suit pas les versions reste fonctionnel."""
    project = (await auth_client.post("/api/projects", json={"name": "Sans version"})).json()
    response = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"description": "ajoutée"}
    )
    assert response.status_code == 200


# --- Pièces et lettrage automatique ------------------------------------------------------------


async def test_creating_a_room_generates_lettered_walls_plus_floor_and_ceiling(
    auth_client: AsyncClient,
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Avec pièce"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms",
        json={"name": "Salon", "polygon": CARRE, "wall_thickness_cm": 12},
    )

    assert response.status_code == 201, response.text
    room = response.json()
    labels = [face["label"] for face in room["faces"]]
    assert labels[:4] == ["A", "B", "C", "D"]
    assert FLOOR_LABEL in labels
    assert CEILING_LABEL in labels

    walls = [face for face in room["faces"] if face["kind"] == "wall"]
    assert len(walls) == 4
    # La face A est le segment entre le 1er et le 2e sommet du polygone.
    face_a = next(face for face in walls if face["label"] == "A")
    assert (face_a["start_x_cm"], face_a["start_y_cm"]) == (0, 0)
    assert (face_a["end_x_cm"], face_a["end_y_cm"]) == (400, 0)
    # Le dernier mur referme le polygone.
    face_d = next(face for face in walls if face["label"] == "D")
    assert (face_d["start_x_cm"], face_d["start_y_cm"]) == (0, 300)
    assert (face_d["end_x_cm"], face_d["end_y_cm"]) == (0, 0)


async def test_a_room_without_polygon_has_no_face_yet(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Esquisse"})).json()
    room = (
        await auth_client.post(f"/api/projects/{project['id']}/rooms", json={"name": "À tracer"})
    ).json()
    assert room["faces"] == []


async def test_growing_the_polygon_adds_walls_and_keeps_the_existing_ones(
    auth_client: AsyncClient,
) -> None:
    """Modifier le plan ne doit pas détruire le revêtement et les éléments déjà posés."""
    project = (await auth_client.post("/api/projects", json={"name": "Évolutif"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Séjour", "polygon": CARRE}
        )
    ).json()

    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.patch(
        f"/api/faces/{face_a['id']}", json={"covering": {"color": "#ff0000"}}
    )
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements", json={"kind": "window", "width_cm": 90}
    )

    pentagone = [*CARRE, [-100, 150]]
    updated = await auth_client.patch(f"/api/rooms/{room['id']}", json={"polygon": pentagone})

    assert updated.status_code == 200
    faces = updated.json()["faces"]
    walls = [face for face in faces if face["kind"] == "wall"]
    assert sorted(face["label"] for face in walls) == ["A", "B", "C", "D", "E"]

    kept = next(face for face in faces if face["label"] == "A")
    assert kept["id"] == face_a["id"], "la face A doit être conservée, pas recréée"
    assert kept["covering"] == {"color": "#ff0000"}, "le revêtement doit survivre"
    assert len(kept["elements"]) == 1, "les éléments posés doivent survivre"


async def test_shrinking_the_polygon_removes_the_extra_walls(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Simplifié"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Pièce", "polygon": [*CARRE, [-100, 150]]},
        )
    ).json()
    assert len([f for f in room["faces"] if f["kind"] == "wall"]) == 5

    updated = await auth_client.patch(f"/api/rooms/{room['id']}", json={"polygon": CARRE})

    walls = [face for face in updated.json()["faces"] if face["kind"] == "wall"]
    assert sorted(face["label"] for face in walls) == ["A", "B", "C", "D"]


@pytest.mark.parametrize(
    "polygon",
    [
        [[0, 0], [100, 0]],  # moins de 3 sommets
        [[0, 0], [0, 0], [100, 100]],  # sommets identiques consécutifs
        [[0, 0, 0], [1, 1, 1], [2, 2, 2]],  # coordonnées à 3 composantes
    ],
)
async def test_an_invalid_polygon_is_refused(
    auth_client: AsyncClient, polygon: list[list[float]]
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Validation"})).json()
    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": polygon}
    )
    assert response.status_code == 422, response.text


async def test_a_negative_wall_thickness_is_refused(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Validation"})).json()
    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms",
        json={"name": "Pièce", "wall_thickness_cm": -5},
    )
    assert response.status_code == 422


# --- Faces et revêtements -----------------------------------------------------------------------


async def test_a_covering_with_an_invalid_colour_is_refused(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Revêtement"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()
    face_id = room["faces"][0]["id"]

    assert (
        await auth_client.patch(f"/api/faces/{face_id}", json={"covering": {"color": "rouge"}})
    ).status_code == 422

    ok = await auth_client.patch(
        f"/api/faces/{face_id}",
        json={"covering": {"color": "#00ff00", "material": "parquet", "pattern": "chevron"}},
    )
    assert ok.status_code == 200
    assert ok.json()["covering"]["pattern"] == "chevron"


async def test_faces_cannot_be_created_or_deleted_directly(auth_client: AsyncClient) -> None:
    """Les faces découlent du polygone : exposer leur création serait une incohérence."""
    project = (await auth_client.post("/api/projects", json={"name": "Faces"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()

    assert (await auth_client.post(f"/api/rooms/{room['id']}/faces", json={})).status_code == 405
    assert (
        await auth_client.delete(f"/api/faces/{room['faces'][0]['id']}")
    ).status_code == 405


# --- Éléments ------------------------------------------------------------------------------------


async def test_create_update_and_delete_an_element(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Éléments"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()
    face_id = room["faces"][0]["id"]

    created = await auth_client.post(
        f"/api/faces/{face_id}/elements",
        json={"kind": "door_hinged", "x_offset_cm": 50, "width_cm": 80, "height_cm": 200},
    )
    assert created.status_code == 201, created.text
    element = created.json()
    assert element["kind"] == "door_hinged"

    updated = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"x_offset_cm": 120}
    )
    assert updated.status_code == 200
    assert updated.json()["x_offset_cm"] == 120

    assert (await auth_client.delete(f"/api/elements/{element['id']}")).status_code == 204


async def test_an_element_referencing_an_unknown_furniture_type_is_refused(
    auth_client: AsyncClient,
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Mobilier"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()

    response = await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": 999_999},
    )
    assert response.status_code == 422


async def test_an_element_can_reference_an_existing_furniture_type(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    furniture = FurnitureType(
        slug="commode", name="Commode", category="bedroom", color_slots=["corps"]
    )
    session.add(furniture)
    await session.commit()

    project = (await auth_client.post("/api/projects", json={"name": "Mobilier"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()

    response = await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={
            "kind": "furniture",
            "furniture_type_id": furniture.id,
            "colors": {"corps": "#8b5a2b"},
            "variant_params": {"nb_tiroirs": 4},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["variant_params"] == {"nb_tiroirs": 4}


async def test_an_invalid_colour_slot_is_refused(auth_client: AsyncClient) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Couleurs"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()

    response = await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={"kind": "furniture", "colors": {"corps": "marron"}},
    )
    assert response.status_code == 422


# --- Cloisonnement entre comptes -----------------------------------------------------------------


async def test_every_route_is_authenticated(client: AsyncClient) -> None:
    routes = [
        ("get", "/api/projects"),
        ("post", "/api/projects"),
        ("get", "/api/projects/1"),
        ("patch", "/api/projects/1"),
        ("delete", "/api/projects/1"),
        ("post", "/api/projects/1/rooms"),
        ("get", "/api/rooms/1"),
        ("patch", "/api/rooms/1"),
        ("delete", "/api/rooms/1"),
        ("get", "/api/rooms/1/faces"),
        ("patch", "/api/faces/1"),
        ("post", "/api/faces/1/elements"),
        ("patch", "/api/elements/1"),
        ("delete", "/api/elements/1"),
    ]
    for method, path in routes:
        # `client.request` : httpx refuse `json=` sur les raccourcis get/delete.
        response = await client.request(method.upper(), path, json={})
        assert response.status_code == 401, f"{method.upper()} {path} → {response.status_code}"


async def test_another_account_cannot_reach_the_whole_tree(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Privé"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()
    face_id = room["faces"][0]["id"]
    element = (
        await auth_client.post(f"/api/faces/{face_id}/elements", json={"kind": "window"})
    ).json()

    # 404 et non 403 : un 403 confirmerait l'existence de l'objet.
    assert (await other_client.get(f"/api/projects/{project['id']}")).status_code == 404
    assert (await other_client.patch(f"/api/projects/{project['id']}", json={})).status_code == 404
    assert (await other_client.delete(f"/api/projects/{project['id']}")).status_code == 404
    assert (await other_client.get(f"/api/rooms/{room['id']}")).status_code == 404
    assert (await other_client.patch(f"/api/rooms/{room['id']}", json={})).status_code == 404
    assert (await other_client.patch(f"/api/faces/{face_id}", json={})).status_code == 404
    assert (
        await other_client.post(f"/api/faces/{face_id}/elements", json={"kind": "window"})
    ).status_code == 404
    assert (
        await other_client.patch(f"/api/elements/{element['id']}", json={})
    ).status_code == 404
    assert (await other_client.delete(f"/api/elements/{element['id']}")).status_code == 404


async def test_a_room_cannot_be_attached_to_someone_elses_project(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Cible"})).json()

    response = await other_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Intrusion"}
    )
    assert response.status_code == 404


# --- Arbre complet -------------------------------------------------------------------------------


async def test_reading_a_project_returns_the_whole_nested_tree(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le viewer 3D (P7) consomme cet arbre : il doit arriver complet en une requête."""
    project = (await auth_client.post("/api/projects", json={"name": "Complet"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Salon", "polygon": CARRE}
        )
    ).json()
    await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements", json={"kind": "window"}
    )

    response = await auth_client.get(f"/api/projects/{project['id']}")

    body = response.json()
    assert len(body["rooms"]) == 1
    assert len(body["rooms"][0]["faces"]) == 6  # 4 murs + sol + plafond
    assert sum(len(face["elements"]) for face in body["rooms"][0]["faces"]) == 1


async def test_deleting_a_project_removes_its_whole_tree(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "À supprimer"})).json()
    await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
    )

    await auth_client.delete(f"/api/projects/{project['id']}")

    session.expunge_all()
    remaining = (await session.execute(select(Project))).scalars().all()
    assert [p.id for p in remaining] == []


# --- Régressions issues de la revue adversariale du ticket P3 -------------------------------


async def _room_with_faces(
    client: AsyncClient, polygon: list[list[float]] | None = None
) -> dict[str, Any]:
    project = (await client.post("/api/projects", json={"name": "Régression"})).json()
    created = await client.post(
        f"/api/projects/{project['id']}/rooms",
        json={"name": "Pièce", "polygon": polygon or CARRE},
    )
    room: dict[str, Any] = created.json()
    return room


async def test_updating_an_element_with_an_invalid_colour_is_refused(
    auth_client: AsyncClient,
) -> None:
    """Régression : `ElementUpdate` ne reportait pas la validation des couleurs.

    La valeur invalide était écrite en base, puis faisait échouer la sérialisation de *toutes*
    les lectures traversant cet élément — l'arbre du projet devenait illisible (500 permanent),
    sans plus aucun moyen de retrouver l'élément fautif par l'API.
    """
    room = await _room_with_faces(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{room['faces'][0]['id']}/elements", json={"kind": "furniture"}
        )
    ).json()

    refused = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"colors": {"corps": "rouge"}}
    )
    assert refused.status_code == 422, refused.text

    # Et surtout : l'arbre reste lisible.
    assert (await auth_client.get(f"/api/rooms/{room['id']}")).status_code == 200
    assert (await auth_client.get(f"/api/rooms/{room['id']}/faces")).status_code == 200


@pytest.mark.parametrize("colour", ["#zzzzzz", "#      ", "#<b>abc", "#-12345", "rouge", "#fff"])
async def test_element_colours_are_as_strict_as_covering_colours(
    auth_client: AsyncClient, colour: str
) -> None:
    """Régression : deux validations divergentes laissaient passer `#zzzzzz` sur un meuble."""
    room = await _room_with_faces(auth_client)
    face_id = room["faces"][0]["id"]

    on_element = await auth_client.post(
        f"/api/faces/{face_id}/elements", json={"kind": "furniture", "colors": {"corps": colour}}
    )
    on_covering = await auth_client.patch(
        f"/api/faces/{face_id}", json={"covering": {"color": colour}}
    )

    assert on_element.status_code == 422, f"{colour!r} accepté sur un meuble"
    assert on_covering.status_code == 422, f"{colour!r} accepté sur un revêtement"


async def test_editing_the_plan_bumps_the_project_version(auth_client: AsyncClient) -> None:
    """Régression : seul `PATCH /projects` incrémentait `version`.

    Le verrouillage optimiste ne protégeait donc rien du plan lui-même — pièces, faces et
    éléments restaient en « dernière écriture gagne », l'option que la spec §8 cas 3 écarte.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Versionné"})).json()
    assert project["version"] == 1

    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": CARRE}
        )
    ).json()
    after_room = (await auth_client.get(f"/api/projects/{project['id']}")).json()["version"]
    assert after_room > 1, "créer une pièce doit faire évoluer la version du projet"

    await auth_client.patch(
        f"/api/faces/{room['faces'][0]['id']}", json={"covering": {"color": "#123456"}}
    )
    after_covering = (await auth_client.get(f"/api/projects/{project['id']}")).json()["version"]
    assert after_covering > after_room, "poser un revêtement doit faire évoluer la version"

    await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements", json={"kind": "window"}
    )
    after_element = (await auth_client.get(f"/api/projects/{project['id']}")).json()["version"]
    assert after_element > after_covering, "poser un élément doit faire évoluer la version"


@pytest.mark.parametrize(
    ("method", "path_template", "body"),
    [
        ("patch", "/api/rooms/{room_id}", {"name": "Renommée", "version": 1}),
        ("patch", "/api/faces/{face_id}", {"covering": {"color": "#abcdef"}, "version": 1}),
        ("post", "/api/faces/{face_id}/elements", {"kind": "window", "version": 1}),
    ],
)
async def test_a_stale_version_is_rejected_on_every_plan_write(
    auth_client: AsyncClient, method: str, path_template: str, body: dict[str, Any]
) -> None:
    room = await _room_with_faces(auth_client)
    # Une première écriture fait passer la version au-delà de 1.
    await auth_client.patch(f"/api/rooms/{room['id']}", json={"name": "Première écriture"})

    path = path_template.format(room_id=room["id"], face_id=room["faces"][0]["id"])
    response = await auth_client.request(method.upper(), path, json=body)

    assert response.status_code == 409, response.text
    assert "X-Current-Version" in response.headers


async def test_inserting_a_vertex_at_the_head_keeps_walls_attached_to_their_geometry(
    auth_client: AsyncClient,
) -> None:
    """Régression : l'appariement des murs était positionnel, pas géométrique.

    Insérer un sommet en tête décale tous les rangs : chaque mur héritait alors du revêtement et
    des meubles de son voisin, et les éléments se retrouvaient hors du mur qui les portait.
    """
    room = await _room_with_faces(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.patch(f"/api/faces/{face_a['id']}", json={"covering": {"color": "#ff0000"}})
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 300, "width_cm": 40, "height_cm": 100},
    )

    # Même pièce physique, décrite à partir d'un autre sommet de départ.
    decale: list[list[float]] = [[-100.0, 150.0], *CARRE]
    updated = await auth_client.patch(f"/api/rooms/{room['id']}", json={"polygon": decale})
    assert updated.status_code == 200, updated.text

    faces = updated.json()["faces"]
    survivor = next(face for face in faces if face["id"] == face_a["id"])
    # Le mur d'origine existe toujours, avec sa géométrie : seul son *rang* a changé.
    assert (survivor["start_x_cm"], survivor["start_y_cm"]) == (0, 0)
    assert (survivor["end_x_cm"], survivor["end_y_cm"]) == (400, 0)
    assert survivor["covering"] == {"color": "#ff0000"}
    assert len(survivor["elements"]) == 1
    assert survivor["elements"][0]["x_offset_cm"] == 300


async def test_emptying_the_polygon_removes_floor_and_ceiling_too(
    auth_client: AsyncClient,
) -> None:
    """Régression : le sol et le plafond survivaient à la disparition du polygone.

    Deux pièces dans le même état (polygone vide) avaient alors des faces différentes selon leur
    seul historique, alors que la règle est « les faces découlent du polygone ».
    """
    room = await _room_with_faces(auth_client)
    assert len(room["faces"]) == 6

    updated = await auth_client.patch(f"/api/rooms/{room['id']}", json={"polygon": []})

    assert updated.status_code == 200, updated.text
    assert updated.json()["faces"] == []


async def test_shrinking_the_polygon_refuses_to_silently_destroy_elements(
    auth_client: AsyncClient,
) -> None:
    """Régression : réduire un polygone supprimait les meubles posés, en 200 OK et sans avertir."""
    pentagone: list[list[float]] = [*CARRE, [-100.0, 150.0]]
    room = await _room_with_faces(auth_client, pentagone)
    face_e = next(face for face in room["faces"] if face["label"] == "E")
    await auth_client.post(
        f"/api/faces/{face_e['id']}/elements",
        json={"kind": "door_hinged", "width_cm": 80, "height_cm": 200},
    )

    refused = await auth_client.patch(f"/api/rooms/{room['id']}", json={"polygon": CARRE})
    assert refused.status_code == 409, refused.text
    assert "force" in refused.json()["detail"]

    # Rien n'a bougé.
    unchanged = (await auth_client.get(f"/api/rooms/{room['id']}")).json()
    assert len([f for f in unchanged["faces"] if f["kind"] == "wall"]) == 5

    # Avec confirmation explicite, l'opération passe.
    confirmed = await auth_client.patch(
        f"/api/rooms/{room['id']}", json={"polygon": CARRE, "force": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    walls = [face for face in confirmed.json()["faces"] if face["kind"] == "wall"]
    assert sorted(face["label"] for face in walls) == ["A", "B", "C", "D"]


async def test_an_element_larger_than_its_wall_is_refused(auth_client: AsyncClient) -> None:
    """Régression : une porte de 9999 par 9999 a x=99999 sur un mur de 400 cm etait acceptee."""
    room = await _room_with_faces(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")

    too_wide = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "door_hinged", "x_offset_cm": 99_999, "width_cm": 9999, "height_cm": 9999},
    )
    assert too_wide.status_code == 422, too_wide.text
    assert "déborde" in too_wide.json()["detail"]

    fits = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "door_hinged", "x_offset_cm": 50, "width_cm": 90, "height_cm": 200},
    )
    assert fits.status_code == 201, fits.text


async def test_moving_an_element_out_of_its_wall_is_refused(auth_client: AsyncClient) -> None:
    room = await _room_with_faces(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    element = (
        await auth_client.post(
            f"/api/faces/{face_a['id']}/elements",
            json={"kind": "window", "x_offset_cm": 50, "width_cm": 90, "height_cm": 100},
        )
    ).json()

    response = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"x_offset_cm": 380}
    )
    assert response.status_code == 422


async def test_an_oversized_json_blob_is_refused(auth_client: AsyncClient) -> None:
    """Régression : `variant_params` acceptait 3 Mo par élément, sans aucune borne."""
    room = await _room_with_faces(auth_client)

    response = await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={
            "kind": "furniture",
            "variant_params": {f"cle{index}": "x" * 100 for index in range(200)},
        },
    )
    assert response.status_code == 422


async def test_the_conflict_schema_is_published_in_openapi(auth_client: AsyncClient) -> None:
    """Le schéma OpenAPI est la source de vérité du frontend : le 409 doit y figurer."""
    schema = (await auth_client.get("/openapi.json")).json()

    for path, method in [
        ("/api/projects/{project_id}", "patch"),
        ("/api/rooms/{room_id}", "patch"),
        ("/api/faces/{face_id}", "patch"),
        ("/api/elements/{element_id}", "patch"),
    ]:
        responses = schema["paths"][path][method]["responses"]
        assert "409" in responses, f"{method.upper()} {path} ne documente pas le conflit"


async def test_clearing_a_covering_is_possible(auth_client: AsyncClient) -> None:
    """`covering: null` doit effacer, et non être un no-op silencieux."""
    room = await _room_with_faces(auth_client)
    face_id = room["faces"][0]["id"]
    await auth_client.patch(f"/api/faces/{face_id}", json={"covering": {"color": "#123456"}})

    cleared = await auth_client.patch(f"/api/faces/{face_id}", json={"covering": None})

    assert cleared.status_code == 200
    assert cleared.json()["covering"] == {}
