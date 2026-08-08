"""Écriture en lot du plan — amendement A6 de `docs/spec-complete.md` §10.

Le verrouillage optimiste (spec §8, cas 3) a une conséquence que la spec n'avait pas anticipée :
chaque écriture incrémente `Project.version` et périme celle que le client détient. Déplacer
quinze meubles imposait donc quinze allers-retours **strictement séquentiels** — le seul geste
naturel de l'éditeur, le glisser-déposer, était le plus mal servi par l'API.

Ce que ce fichier vérifie, dans l'ordre d'importance :

1. **le cloisonnement** — un identifiant d'un autre projet dans le corps de la requête est traité
   comme inexistant. C'est le risque propre à cette route : l'appartenance est vérifiée une fois,
   sur le projet de l'URL, et ne dit rien des identifiants que le client a mis dans son lot ;
2. **le tout ou rien** — une opération refusée annule le lot entier et nomme son rang ;
3. **la version unique** — quinze déplacements ne consomment qu'une version.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.plan import MAX_BATCH_OPERATIONS
from app.services.seed import seed_catalog

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def _plan(client: AsyncClient, name: str = "Chantier") -> dict[str, Any]:
    project = (await client.post("/api/projects", json={"name": name})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Séjour", "polygon": CARRE}
        )
    ).json()
    return {"project": project, "room": room, "face": room["faces"][0]}


def _free(pos_x: float, pos_y: float) -> dict[str, Any]:
    return {
        "kind": "furniture", "pos_x_cm": pos_x, "pos_y_cm": pos_y,
        "width_cm": 60, "height_cm": 70, "depth_cm": 40,
    }


async def _batch(
    client: AsyncClient, project_id: int, operations: list[dict[str, Any]], **extra: Any
) -> Any:
    return await client.post(
        f"/api/projects/{project_id}/batch", json={"operations": operations} | extra
    )


# --- La raison d'être : une seule version pour tout un geste --------------------------------------


async def test_fifteen_moves_consume_a_single_version(auth_client: AsyncClient) -> None:
    """Le chiffre du ticket : quinze meubles déplacés, une version, un aller-retour."""
    plan = await _plan(auth_client)
    identifiers = []
    for index in range(15):
        created = await auth_client.post(
            f"/api/rooms/{plan['room']['id']}/elements",
            json=_free(50 + index * 20, 50),
        )
        assert created.status_code == 201, created.text
        identifiers.append(created.json()["id"])

    before = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [
            {"op": "update_element", "element_id": element_id,
             "changes": {"pos_x_cm": 60 + index * 20, "pos_y_cm": 200}}
            for index, element_id in enumerate(identifiers)
        ],
        version=before,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == before + 1
    assert len(body["results"]) == 15
    assert [result["element"]["pos_y_cm"] for result in body["results"]] == [200.0] * 15


async def test_the_results_come_back_in_the_order_they_were_sent(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)
    lit = (await auth_client.get("/api/furniture-types/lit")).json()
    plan = await _plan(auth_client)
    doomed = (
        await auth_client.post(f"/api/rooms/{plan['room']['id']}/elements", json=_free(100, 100))
    ).json()

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [
            {"op": "create_room", "room": {"name": "Cuisine", "polygon": CARRE}},
            {"op": "create_face_element", "face_id": plan["face"]["id"],
             "element": {"kind": "window", "x_offset_cm": 40, "y_offset_cm": 100,
                         "width_cm": 90, "height_cm": 110}},
            {"op": "create_room_element", "room_id": plan["room"]["id"],
             "element": _free(200, 150) | {"furniture_type_id": lit["id"]}},
            {"op": "delete_element", "element_id": doomed["id"]},
            {"op": "update_room", "room_id": plan["room"]["id"],
             "changes": {"name": "Salon"}},
        ],
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [result["op"] for result in results] == [
        "create_room",
        "create_face_element",
        "create_room_element",
        "delete_element",
        "update_room",
    ]
    assert [result["status"] for result in results] == [
        "created", "created", "created", "deleted", "updated"
    ]
    # Une création de pièce rend ses faces : le client a besoin de leurs identifiants tout de suite.
    assert len(results[0]["room"]["faces"]) == 6
    assert results[1]["element"]["kind"] == "window"
    assert results[2]["element"]["room_id"] == plan["room"]["id"]
    # Une suppression n'a plus d'objet à rendre : son identifiant est tout ce qu'il en reste.
    assert results[3]["element"] is None and results[3]["element_id"] == doomed["id"]
    assert results[4]["room"]["name"] == "Salon"


# --- Cloisonnement -------------------------------------------------------------------------------


async def test_an_element_of_another_project_is_treated_as_inexistent(
    auth_client: AsyncClient,
) -> None:
    """Le risque propre au lot : l'appartenance est vérifiée sur l'URL, pas sur le corps.

    Les deux projets appartiennent ici au **même** compte : ce n'est donc pas le cloisonnement
    entre locataires qui refuse, c'est bien la vérification du projet de chaque identifiant. Sans
    elle, un lot adressé à un projet écrirait dans un autre.
    """
    mien = await _plan(auth_client, "Le mien")
    autre = await _plan(auth_client, "L'autre")
    chez_lautre = (
        await auth_client.post(f"/api/rooms/{autre['room']['id']}/elements", json=_free(100, 100))
    ).json()

    response = await _batch(
        auth_client,
        mien["project"]["id"],
        [{"op": "update_element", "element_id": chez_lautre["id"],
          "changes": {"pos_x_cm": 10}}],
    )

    assert response.status_code == 404, response.text
    # Rien n'a bougé chez le voisin.
    room = (await auth_client.get(f"/api/rooms/{autre['room']['id']}")).json()
    assert room["free_elements"][0]["pos_x_cm"] == 100.0


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "create_face_element", "face_id": 0,
         "element": {"kind": "furniture", "width_cm": 50, "height_cm": 50}},
        {"op": "create_room_element", "room_id": 0, "element": {}},
        {"op": "update_room", "room_id": 0, "changes": {"name": "Détournée"}},
        {"op": "delete_room", "room_id": 0},
        {"op": "delete_element", "element_id": 0},
    ],
)
async def test_every_kind_of_foreign_identifier_is_refused(
    auth_client: AsyncClient, operation: dict[str, Any]
) -> None:
    mien = await _plan(auth_client, "Le mien")
    autre = await _plan(auth_client, "L'autre")
    etranger = (
        await auth_client.post(f"/api/rooms/{autre['room']['id']}/elements", json=_free(100, 100))
    ).json()
    substitutions = {
        "face_id": autre["face"]["id"],
        "room_id": autre["room"]["id"],
        "element_id": etranger["id"],
    }
    payload = {
        key: substitutions.get(key, value) if key.endswith("_id") else value
        for key, value in operation.items()
    }
    if payload["op"] == "create_room_element":
        payload["element"] = _free(100, 100)

    response = await _batch(auth_client, mien["project"]["id"], [payload])

    assert response.status_code == 404, response.text


# --- Tout ou rien --------------------------------------------------------------------------------


async def test_a_refused_operation_cancels_the_whole_batch_and_names_its_rank(
    auth_client: AsyncClient,
) -> None:
    """Un lot à moitié appliqué laisserait le client dans un état qu'il ne peut pas reconstituer."""
    plan = await _plan(auth_client)
    before = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [
            {"op": "create_room_element", "room_id": plan["room"]["id"],
             "element": _free(100, 100)},
            {"op": "create_room", "room": {"name": "Cuisine", "polygon": CARRE}},
            # Hors du polygone : refusée.
            {"op": "create_room_element", "room_id": plan["room"]["id"],
             "element": _free(900, 900)},
        ],
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "opération n° 3" in detail
    assert "create_room_element" in detail
    assert "Le lot entier est annulé" in detail

    project = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()
    assert project["version"] == before, "la version a bougé alors que rien n'a été écrit"
    assert len(project["rooms"]) == 1, "la pièce de l'opération n° 2 a survécu au refus"
    assert project["rooms"][0]["free_elements"] == []


async def test_a_destructive_reshape_inside_a_batch_asks_for_confirmation(
    auth_client: AsyncClient,
) -> None:
    """Perdre des ouvertures en réponse à un 200 reste une perte de données invisible."""
    plan = await _plan(auth_client)
    await auth_client.post(
        f"/api/faces/{plan['face']['id']}/elements",
        json={"kind": "window", "x_offset_cm": 40, "y_offset_cm": 100,
              "width_cm": 90, "height_cm": 110},
    )
    triangle = [[0, 0], [400, 300], [0, 300]]

    refused = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "update_room", "room_id": plan["room"]["id"], "changes": {"polygon": triangle}}],
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "destructive_change"
    assert "opération n° 1" in refused.json()["detail"]

    confirmed = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "update_room", "room_id": plan["room"]["id"],
          "changes": {"polygon": triangle, "force": True}}],
    )
    assert confirmed.status_code == 200, confirmed.text


async def test_an_unknown_furniture_type_is_caught_before_anything_is_written(
    auth_client: AsyncClient,
) -> None:
    plan = await _plan(auth_client)

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [
            {"op": "create_room_element", "room_id": plan["room"]["id"],
             "element": _free(100, 100) | {"furniture_type_id": 999_999}},
        ],
    )

    assert response.status_code == 422, response.text
    assert "999999" in response.json()["detail"]


# --- Verrouillage optimiste ----------------------------------------------------------------------


async def test_a_stale_version_refuses_the_batch_before_touching_anything(
    auth_client: AsyncClient,
) -> None:
    plan = await _plan(auth_client)
    current = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "create_room_element", "room_id": plan["room"]["id"],
          "element": _free(100, 100)}],
        version=current - 1,
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "stale_version"
    assert response.json()["current_version"] == current
    assert response.headers["X-Current-Version"] == str(current)

    room = (await auth_client.get(f"/api/rooms/{plan['room']['id']}")).json()
    assert room["free_elements"] == []


async def test_a_batch_without_a_version_still_applies(auth_client: AsyncClient) -> None:
    """La version reste facultative, comme sur toutes les écritures unitaires du plan."""
    plan = await _plan(auth_client)

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "create_room_element", "room_id": plan["room"]["id"],
          "element": _free(100, 100)}],
    )

    assert response.status_code == 200, response.text


# --- Bornes et forme du contrat ------------------------------------------------------------------


async def test_the_number_of_operations_is_bounded(auth_client: AsyncClient) -> None:
    """Sans borne, une requête tient une transaction ouverte aussi longtemps qu'elle veut."""
    plan = await _plan(auth_client)
    operation = {
        "op": "create_room_element", "room_id": plan["room"]["id"], "element": _free(100, 100)
    }

    accepted = await _batch(
        auth_client, plan["project"]["id"], [operation] * MAX_BATCH_OPERATIONS
    )
    refused = await _batch(
        auth_client, plan["project"]["id"], [operation] * (MAX_BATCH_OPERATIONS + 1)
    )

    assert accepted.status_code == 200, accepted.text
    assert refused.status_code == 422, refused.text


async def test_an_empty_batch_is_refused(auth_client: AsyncClient) -> None:
    """Un lot vide est une requête qui incrémenterait la version sans rien écrire."""
    plan = await _plan(auth_client)

    assert (await _batch(auth_client, plan["project"]["id"], [])).status_code == 422


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "supprime_tout"},
        {"op": "delete_element"},
        {"op": "delete_element", "element_id": 1, "de_trop": True},
        {"op": "create_room_element", "room_id": 1, "element": {"pos_x_cm": 10}},
    ],
)
async def test_a_malformed_operation_is_refused_by_the_schema(
    auth_client: AsyncClient, operation: dict[str, Any]
) -> None:
    """L'union est discriminée sur `op` : le message nomme l'opération, pas la dernière variante."""
    plan = await _plan(auth_client)

    assert (
        await _batch(auth_client, plan["project"]["id"], [operation])
    ).status_code == 422


async def test_an_opening_cannot_be_anchored_to_a_room_through_a_batch(
    auth_client: AsyncClient,
) -> None:
    """Le lot ne doit pas être un chemin de contournement des règles des routes unitaires."""
    plan = await _plan(auth_client)

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "create_room_element", "room_id": plan["room"]["id"],
          "element": {"kind": "window", "pos_x_cm": 100, "pos_y_cm": 100}}],
    )

    assert response.status_code == 422, response.text


async def test_a_batch_cannot_write_a_placement_in_the_other_frame(
    auth_client: AsyncClient,
) -> None:
    plan = await _plan(auth_client)
    libre = (
        await auth_client.post(f"/api/rooms/{plan['room']['id']}/elements", json=_free(100, 100))
    ).json()

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "update_element", "element_id": libre["id"], "changes": {"x_offset_cm": 10}}],
    )

    assert response.status_code == 422, response.text
    assert "autre repère" in response.json()["detail"]


async def test_a_viewer_cannot_apply_a_batch(auth_client: AsyncClient) -> None:
    """Écrire le plan demande `editor` : le lot suit exactement les routes qu'il remplace."""
    from tests.test_permissions_locataire import logged_in

    plan = await _plan(auth_client)
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]

    async with logged_in("lecteur-lot@exemple.fr") as lecteur:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "lecteur-lot@exemple.fr", "role": "viewer"},
        )
        await lecteur.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )

        refused = await _batch(
            lecteur,
            plan["project"]["id"],
            [{"op": "create_room_element", "room_id": plan["room"]["id"],
              "element": _free(100, 100)}],
        )

    assert refused.status_code == 403, refused.text


async def test_the_batch_invalidates_the_scene_cache_through_the_version(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """La clé du cache porte la version du projet : une seule incrémentation doit suffire."""
    await seed_catalog(session)
    lit = (await auth_client.get("/api/furniture-types/lit")).json()
    plan = await _plan(auth_client)
    before = (await auth_client.get(f"/api/projects/{plan['project']['id']}/scene")).json()
    assert not [node for node in before["rooms"][0]["nodes"] if node["kind"] == "furniture"]

    await _batch(
        auth_client,
        plan["project"]["id"],
        [{"op": "create_room_element", "room_id": plan["room"]["id"],
          "element": _free(200, 150) | {"furniture_type_id": lit["id"]}}],
    )

    after = (await auth_client.get(f"/api/projects/{plan['project']['id']}/scene")).json()
    furniture = [node for node in after["rooms"][0]["nodes"] if node["kind"] == "furniture"]
    assert len(furniture) == 1
    assert furniture[0]["face_label"] is None


async def test_designating_something_the_batch_already_deleted_conflicts(
    auth_client: AsyncClient,
) -> None:
    """Un lot mal ordonné doit échouer proprement, pas corrompre le plan.

    La ligne visée n'existe plus quand la modification arrive : c'est la base qui le constate, et
    la collision remonte en 409. Rien n'est écrit, et le message dit au client de recharger.
    """
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(f"/api/rooms/{plan['room']['id']}/elements", json=_free(100, 100))
    ).json()

    response = await _batch(
        auth_client,
        plan["project"]["id"],
        [
            {"op": "delete_room", "room_id": plan["room"]["id"]},
            {"op": "update_element", "element_id": element["id"], "changes": {"pos_x_cm": 120}},
        ],
    )

    assert response.status_code == 409, response.text
    project = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()
    assert len(project["rooms"]) == 1, "la pièce a été supprimée malgré le refus du lot"
