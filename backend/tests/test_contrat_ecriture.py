"""Le contrat d'écriture de l'API du plan (lot L5).

Deux sujets, indissociables dans les faits :

- **le conflit** — corps du 409, champ `code` sur lequel le frontend aiguille, et couverture des
  trois `DELETE`, jusqu'ici les seules écritures restées en « dernière écriture gagne » ;
- **la géométrie métier** — encombrement par axe, recouvrement des ouvertures, revalidation
  déclenchée par autre chose que le polygone.

Référence : `docs/spec-complete.md` §8 (cas 3) et §3.1.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import col, select

from app.models.plan import Element, Face, Project, Room

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def _plan(client: AsyncClient, polygon: list[list[float]] | None = None) -> dict[str, Any]:
    """Projet d'une pièce, renvoyé avec sa pièce et ses faces."""
    project = (await client.post("/api/projects", json={"name": "Contrat"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Pièce", "polygon": polygon or CARRE},
        )
    ).json()
    return {"project": project, "room": room}


def _face(room: dict[str, Any], label: str) -> dict[str, Any]:
    face: dict[str, Any] = next(entry for entry in room["faces"] if entry["label"] == label)
    return face


class StatementCounter:
    """Compte les instructions SQL réellement émises sur le moteur de test."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine.sync_engine
        self.statements: list[str] = []

    def __enter__(self) -> "StatementCounter":
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    def _record(self, _conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        self.statements.append(statement)

    def selects(self) -> int:
        return sum(1 for entry in self.statements if entry.lstrip().upper().startswith("SELECT"))


# --- Corps du 409 -------------------------------------------------------------------------------


async def test_the_conflict_body_carries_the_current_version_and_a_code(
    auth_client: AsyncClient,
) -> None:
    """Régression : `current_version` était déclaré obligatoire dans l'OpenAPI et jamais envoyé.

    `responses=` est purement documentaire — il ne fabrique aucun corps. Le client lisait donc un
    schéma qui ne correspondait à rien de ce qu'il recevait.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Conflit"})).json()
    await auth_client.patch(f"/api/projects/{project['id']}", json={"name": "Première"})

    refused = await auth_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Perdante", "version": 1}
    )

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["current_version"] == 2
    assert body["code"] == "stale_version"
    assert refused.headers["X-Current-Version"] == "2"


async def test_a_destructive_change_is_flagged_by_its_code_and_not_by_its_message(
    auth_client: AsyncClient,
) -> None:
    """Le frontend distinguait les deux conflits par une sous-chaîne du message français."""
    pentagone: list[list[float]] = [*CARRE, [-100.0, 150.0]]
    plan = await _plan(auth_client, pentagone)
    face_e = _face(plan["room"], "E")
    await auth_client.post(
        f"/api/faces/{face_e['id']}/elements",
        json={"kind": "door_hinged", "width_cm": 80, "height_cm": 200},
    )

    refused = await auth_client.patch(
        f"/api/rooms/{plan['room']['id']}", json={"polygon": CARRE}
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "destructive_change"
    assert refused.json()["current_version"] is not None


async def test_the_conflict_schema_documents_the_code(auth_client: AsyncClient) -> None:
    """Le schéma OpenAPI est la source de vérité du frontend : les deux codes doivent y figurer.

    Les nommer ici verrouille le contrat que l'éditeur implémente en face — un renommage côté
    serveur casse ce test avant de casser silencieusement le rejeu côté client.
    """
    schema = (await auth_client.get("/openapi.json")).json()
    conflict = schema["components"]["schemas"]["ConflictDetail"]["properties"]

    assert set(conflict) == {"detail", "current_version", "code"}
    assert set(conflict["code"]["enum"]) == {"stale_version", "destructive_change"}

    for path, method in [
        ("/api/projects/{project_id}", "delete"),
        ("/api/rooms/{room_id}", "delete"),
        ("/api/elements/{element_id}", "delete"),
    ]:
        responses = schema["paths"][path][method]["responses"]
        assert "409" in responses, f"{method.upper()} {path} ne documente pas le conflit"


# --- Les trois DELETE ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["project", "room", "element"])
async def test_a_delete_with_a_stale_version_changes_nothing(
    auth_client: AsyncClient, session: AsyncSession, target: str
) -> None:
    """Le cœur du lot : les routes les plus destructrices étaient les seules non protégées.

    Sans `version`, deux onglets ouverts sur le même plan supprimaient le travail l'un de l'autre
    en 204, exactement l'option « dernière écriture gagne » que la spec §8 (cas 3) écarte.
    """
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements", json={"kind": "window"}
        )
    ).json()
    paths = {
        "project": f"/api/projects/{plan['project']['id']}",
        "room": f"/api/rooms/{plan['room']['id']}",
        "element": f"/api/elements/{element['id']}",
    }
    models: dict[str, type[Project] | type[Room] | type[Element]] = {
        "project": Project,
        "room": Room,
        "element": Element,
    }

    refused = await auth_client.delete(paths[target], params={"version": 1})

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "stale_version"
    session.expunge_all()
    remaining = (await session.execute(select(models[target]))).scalars().all()
    assert len(remaining) == 1, f"le {target} a été supprimé malgré le conflit"


@pytest.mark.parametrize("target", ["project", "room", "element"])
async def test_a_delete_with_the_right_version_goes_through(
    auth_client: AsyncClient, target: str
) -> None:
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements", json={"kind": "window"}
        )
    ).json()
    version = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]
    paths = {
        "project": f"/api/projects/{plan['project']['id']}",
        "room": f"/api/rooms/{plan['room']['id']}",
        "element": f"/api/elements/{element['id']}",
    }

    accepted = await auth_client.delete(paths[target], params={"version": version})

    assert accepted.status_code == 204, accepted.text


async def test_deleting_a_room_bumps_the_project_version(auth_client: AsyncClient) -> None:
    """Sinon la scène en cache reste servie telle qu'elle était avant la suppression."""
    plan = await _plan(auth_client)
    before = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]

    await auth_client.delete(f"/api/rooms/{plan['room']['id']}")

    after = (await auth_client.get(f"/api/projects/{plan['project']['id']}")).json()["version"]
    assert after > before


async def test_deleting_a_project_does_not_walk_its_tree(
    auth_client: AsyncClient, engine: AsyncEngine
) -> None:
    """Mesure : la suppression émettait un SELECT par pièce, par face et par élément.

    Le nombre d'instructions doit désormais être indépendant de la taille du plan — c'est la base
    qui exécute la cascade (`passive_deletes` + `ON DELETE CASCADE`).
    """
    small = (await auth_client.post("/api/projects", json={"name": "Petit"})).json()
    await auth_client.post(
        f"/api/projects/{small['id']}/rooms", json={"name": "P", "polygon": CARRE}
    )
    large = (await auth_client.post("/api/projects", json={"name": "Grand"})).json()
    for index in range(10):
        offset = index * 500
        await auth_client.post(
            f"/api/projects/{large['id']}/rooms",
            json={"name": f"P{index}", "polygon": [[x + offset, y] for x, y in CARRE]},
        )

    with StatementCounter(engine) as on_small:
        await auth_client.delete(f"/api/projects/{small['id']}")
    with StatementCounter(engine) as on_large:
        await auth_client.delete(f"/api/projects/{large['id']}")

    assert on_small.selects() == on_large.selects(), (
        f"{on_small.selects()} SELECT pour 1 pièce contre {on_large.selects()} pour 10 : "
        "la suppression parcourt encore l'arbre"
    )


# --- Encombrement : les axes ne se mélangent pas ------------------------------------------------


async def test_a_wardrobe_taller_than_the_room_is_deep_fits_on_the_floor(
    auth_client: AsyncClient,
) -> None:
    """Régression : la hauteur d'un meuble était comparée à l'étendue **au sol**.

    Une armoire de 120 x 200 x 60 dans une pièce de 400 x 300 était refusée, alors qu'elle tient
    très largement : `y_offset + height` était confronté à la profondeur de la pièce.
    """
    plan = await _plan(auth_client)
    sol = _face(plan["room"], "SOL")

    response = await auth_client.post(
        f"/api/faces/{sol['id']}/elements",
        json={"kind": "furniture", "width_cm": 120, "height_cm": 200, "depth_cm": 60},
    )

    assert response.status_code == 201, response.text


async def test_a_bed_deeper_than_the_room_is_refused(auth_client: AsyncClient) -> None:
    """L'autre moitié de la même erreur : la profondeur n'était comparée à rien.

    Un lit de 140 x 45 x 200 posé à 200 cm du bord traversait le mur d'en face sans un mot.
    """
    plan = await _plan(auth_client)
    sol = _face(plan["room"], "SOL")

    response = await auth_client.post(
        f"/api/faces/{sol['id']}/elements",
        json={"kind": "furniture", "y_offset_cm": 200,
              "width_cm": 140, "height_cm": 45, "depth_cm": 200},
    )

    assert response.status_code == 422, response.text
    assert "profondeur" in response.json()["detail"]


async def test_a_furniture_taller_than_the_ceiling_is_refused(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)
    sol = _face(plan["room"], "SOL")

    response = await auth_client.post(
        f"/api/faces/{sol['id']}/elements",
        json={"kind": "furniture", "width_cm": 100, "height_cm": 260, "depth_cm": 40},
    )

    assert response.status_code == 422, response.text
    assert "plus haut que la pièce" in response.json()["detail"]


@pytest.mark.parametrize("overshoot", [0.9, 1.0, 5.0])
async def test_an_opening_may_not_overshoot_its_wall_by_a_hair(
    auth_client: AsyncClient, overshoot: float
) -> None:
    """Régression : la tolérance de `+ 1` cm laissait un trou sortir du contour du mur.

    `earcut` ne sait pas trianguler un trou qui déborde : sur un mur de 400 x 250, l'aire calculée
    passait de 100 000 à 163 258 cm². Le mur explosait visuellement pour 9 mm.
    """
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 400 - 90 + overshoot,
              "width_cm": 90, "height_cm": 110},
    )

    assert response.status_code == 422, response.text


async def test_an_opening_flush_with_the_end_of_its_wall_is_accepted(
    auth_client: AsyncClient,
) -> None:
    """La tolérance retirée ne doit pas transformer un placement bord à bord en refus."""
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 310, "width_cm": 90, "height_cm": 110},
    )

    assert response.status_code == 201, response.text


# --- Recouvrement des ouvertures ----------------------------------------------------------------


async def test_two_overlapping_openings_are_refused(auth_client: AsyncClient) -> None:
    """Régression : deux trous sécants retiraient 5 860 cm² au lieu des 27 460 de leur union.

    `earcut` n'accepte pas des trous qui se croisent : il en retire la différence symétrique, et
    le mur apparaît percé de biais.
    """
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")
    first = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 50, "y_offset_cm": 90,
              "width_cm": 160, "height_cm": 110},
    )
    assert first.status_code == 201, first.text

    second = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 150, "y_offset_cm": 150,
              "width_cm": 160, "height_cm": 90},
    )

    assert second.status_code == 422, second.text
    assert "recouvre" in second.json()["detail"]


async def test_two_openings_side_by_side_are_accepted(auth_client: AsyncClient) -> None:
    """Deux fenêtres bord à bord ne se croisent pas : elles se touchent."""
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 0, "y_offset_cm": 90,
              "width_cm": 100, "height_cm": 110},
    )

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 100, "y_offset_cm": 90,
              "width_cm": 100, "height_cm": 110},
    )

    assert response.status_code == 201, response.text


async def test_moving_an_opening_onto_another_is_refused(auth_client: AsyncClient) -> None:
    """Le contrôle doit valoir à la modification, sinon il se contourne en deux requêtes."""
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 0, "y_offset_cm": 90,
              "width_cm": 100, "height_cm": 110},
    )
    movable = (
        await auth_client.post(
            f"/api/faces/{face_a['id']}/elements",
            json={"kind": "window", "x_offset_cm": 200, "y_offset_cm": 90,
                  "width_cm": 100, "height_cm": 110},
        )
    ).json()

    response = await auth_client.patch(
        f"/api/elements/{movable['id']}", json={"x_offset_cm": 50}
    )

    assert response.status_code == 422, response.text


async def test_furniture_may_overlap_an_opening(auth_client: AsyncClient) -> None:
    """La règle porte sur les percements, pas sur les meubles : une commode sous une fenêtre est
    parfaitement normale."""
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 50, "y_offset_cm": 90,
              "width_cm": 160, "height_cm": 110},
    )

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "furniture", "x_offset_cm": 60, "y_offset_cm": 100,
              "width_cm": 100, "height_cm": 85},
    )

    assert response.status_code == 201, response.text


# --- Cohérence d'un élément ---------------------------------------------------------------------


async def test_an_opening_cannot_be_laid_on_the_floor(auth_client: AsyncClient) -> None:
    """Régression : une fenêtre au sol était stockée, listée en 2D, et absente de la 3D.

    Le calcul du scene graph ne perce que les murs : l'élément existait dans l'éditeur et nulle
    part ailleurs, sans le moindre message.
    """
    plan = await _plan(auth_client)

    for label in ("SOL", "PLAFOND"):
        response = await auth_client.post(
            f"/api/faces/{_face(plan['room'], label)['id']}/elements",
            json={"kind": "window", "width_cm": 90, "height_cm": 110},
        )
        assert response.status_code == 422, response.text
        assert "percement" in response.json()["detail"]


async def test_an_opening_may_not_carry_furniture_attributes(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "colors": {"corps": "#8b5a2b"}},
    )

    assert response.status_code == 422, response.text
    assert "colors" in response.json()["detail"]


async def test_a_partial_patch_cannot_smuggle_furniture_attributes_onto_an_opening(
    auth_client: AsyncClient,
) -> None:
    """La cohérence se vérifie sur l'état **fusionné**.

    Valider la seule charge utile laisse la règle se contourner en deux requêtes : on pose un
    meuble avec sa recette et ses couleurs, puis on le convertit en fenêtre.
    """
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements",
            json={"kind": "furniture", "colors": {"corps": "#8b5a2b"},
                  "variant_params": {"nb_tiroirs": 4}},
        )
    ).json()

    response = await auth_client.patch(f"/api/elements/{element['id']}", json={"kind": "window"})

    assert response.status_code == 422, response.text


async def test_an_unknown_furniture_type_on_a_stale_version_still_answers_409(
    auth_client: AsyncClient,
) -> None:
    """Régression : `_check_furniture_type` s'exécutait après `_claim_project`.

    Le projet étant déjà marqué modifié, son `SELECT` déclenchait un autoflush, la collision de
    version remontait en `StaleDataError` non rattrapée, et un conflit parfaitement légitime
    sortait en 500.
    """
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements", json={"kind": "furniture"}
        )
    ).json()

    response = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"furniture_type_id": 999_999, "version": 1}
    )

    assert response.status_code in (409, 422), response.text
    assert response.status_code != 500


# --- Revalidation déclenchée par autre chose que le polygone -----------------------------------


async def test_lowering_the_ceiling_refits_the_openings(auth_client: AsyncClient) -> None:
    """Régression : abaisser `ceiling_height_cm` ne redéclenchait aucune vérification.

    La fenêtre restait à sa hauteur d'origine, sortait du mur, et son trou tombait hors du contour
    extrudé — invisible dans l'éditeur 2D, absurde en 3D.
    """
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 50, "y_offset_cm": 140,
              "width_cm": 90, "height_cm": 100},
    )

    updated = await auth_client.patch(
        f"/api/rooms/{plan['room']['id']}", json={"ceiling_height_cm": 200}
    )

    assert updated.status_code == 200, updated.text
    window = _face(updated.json(), "A")["elements"][0]
    assert window["y_offset_cm"] + window["height_cm"] <= 200, (
        f"la fenêtre déborde toujours : y={window['y_offset_cm']} h={window['height_cm']}"
    )


async def test_changing_the_wall_thickness_revalidates_too(auth_client: AsyncClient) -> None:
    """L'épaisseur change l'emprise des murs : elle doit passer par la même resynchronisation."""
    plan = await _plan(auth_client)

    updated = await auth_client.patch(
        f"/api/rooms/{plan['room']['id']}", json={"wall_thickness_cm": 30}
    )

    assert updated.status_code == 200, updated.text
    assert len([face for face in updated.json()["faces"] if face["kind"] == "wall"]) == 4


# --- Contours refusés ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("polygon", "why"),
    [
        ([[0, 0], [400, 0], [0, 300], [400, 300]], "nœud papillon"),
        ([[0, 0], [400, 0], [400, 300], [0, 300], [0, 300.5]], "mur de 5 mm"),
        ([[0, 0], [5, 0], [5, 5], [0, 5]], "pièce de 25 cm²"),
        ([[0, 0], [400, 0], [800, 0]], "sommets alignés, aire nulle"),
    ],
)
async def test_a_degenerate_contour_is_refused(
    auth_client: AsyncClient, polygon: list[list[float]], why: str
) -> None:
    """Un contour qui se croise n'a plus d'aire signée fiable.

    `ensure_counter_clockwise` bascule alors d'un appel à l'autre, et toutes les normales
    sortantes de la pièce s'inversent d'un coup : le logement est vu de l'extérieur.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Contour"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": polygon}
    )

    assert response.status_code == 422, f"{why} accepté : {response.text}"


async def test_a_concave_contour_is_still_accepted(auth_client: AsyncClient) -> None:
    """Le durcissement ne doit pas refuser une pièce en L, cas nominal de la spec §1."""
    en_l: list[list[float]] = [[0, 0], [600, 0], [600, 400], [300, 400], [300, 250], [0, 250]]
    project = (await auth_client.post("/api/projects", json={"name": "En L"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Pièce", "polygon": en_l}
    )

    assert response.status_code == 201, response.text


# --- Blobs JSON : les valeurs aussi ---------------------------------------------------------------


async def test_a_single_huge_json_value_is_refused(auth_client: AsyncClient) -> None:
    """Régression : seul le **nombre** d'entrées était borné.

    `{"note": "<30 Mo>"}` fait une entrée : la charge passait tous les contrôles.
    """
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "furniture", "variant_params": {"note": "x" * 100_000}},
    )

    assert response.status_code == 422, response.text


async def test_a_huge_json_key_is_refused(auth_client: AsyncClient) -> None:
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "furniture", "colors": {"c" * 10_000: "#8b5a2b"}},
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("path_kind", "body"),
    [
        ("element", {"width_cm": None}),
        ("element", {"kind": None}),
        ("element", {"x_offset_cm": None}),
        ("room", {"name": None}),
        ("room", {"polygon": None}),
        ("room", {"ceiling_height_cm": None}),
        ("project", {"name": None}),
    ],
)
async def test_a_meaningless_null_is_refused_instead_of_crashing(
    auth_client: AsyncClient, path_kind: str, body: dict[str, Any]
) -> None:
    """Régression : `{"width_cm": null}` écrivait NULL dans une colonne `NOT NULL`.

    Le champ était déclaré `X | None` pour dire « facultatif », et rien ne distinguait « absent »
    de « à null ». N'importe quel compte authentifié fabriquait ainsi une 500 sur n'importe
    quelle route de modification.
    """
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements", json={"kind": "furniture"}
        )
    ).json()
    paths = {
        "element": f"/api/elements/{element['id']}",
        "room": f"/api/rooms/{plan['room']['id']}",
        "project": f"/api/projects/{plan['project']['id']}",
    }

    response = await auth_client.patch(paths[path_kind], json=body)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("blob", ["colors", "variant_params"])
async def test_a_null_blob_clears_it(auth_client: AsyncClient, blob: str) -> None:
    """`null` efface, exactement comme `covering: null` sur une face."""
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements",
            json={"kind": "furniture", "colors": {"corps": "#8b5a2b"},
                  "variant_params": {"nb_tiroirs": 4}},
        )
    ).json()

    response = await auth_client.patch(f"/api/elements/{element['id']}", json={blob: None})

    assert response.status_code == 200, response.text
    assert response.json()[blob] == {}


async def test_detaching_an_element_from_the_catalogue_stays_possible(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """`furniture_type_id: null` reste un nul porteur de sens : c'est l'éditeur qui l'envoie."""
    from app.models.plan import FurnitureType

    session.add(FurnitureType(slug="table", name="Table", category="general"))
    await session.commit()
    catalogue = (await auth_client.get("/api/furniture-types/table")).json()
    plan = await _plan(auth_client)
    element = (
        await auth_client.post(
            f"/api/faces/{_face(plan['room'], 'A')['id']}/elements",
            json={"kind": "furniture", "furniture_type_id": catalogue["id"]},
        )
    ).json()

    response = await auth_client.patch(
        f"/api/elements/{element['id']}", json={"furniture_type_id": None}
    )

    assert response.status_code == 200, response.text
    assert response.json()["furniture_type_id"] is None


async def test_an_ordinary_variant_parameter_keeps_its_type(auth_client: AsyncClient) -> None:
    """Le durcissement ne doit pas relire `4` en `4.0` ni `true` en `1`."""
    plan = await _plan(auth_client)
    face_a = _face(plan["room"], "A")

    response = await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "furniture", "variant_params": {"nb_tiroirs": 4, "porte": True,
                                                      "jeu": 0.5, "finition": "chêne"}},
    )

    assert response.status_code == 201, response.text
    assert response.json()["variant_params"] == {
        "nb_tiroirs": 4, "porte": True, "jeu": 0.5, "finition": "chêne"
    }


# --- Recherche du catalogue -----------------------------------------------------------------------


@pytest.mark.parametrize("needle", ["_", "%", "%o%"])
async def test_the_catalogue_search_escapes_wildcards(
    auth_client: AsyncClient, session: AsyncSession, needle: str
) -> None:
    """Régression : chercher `_` ramenait tout le catalogue.

    Les jokers du `LIKE` partaient tels quels dans le motif : la recherche devenait un scan
    complet déguisé, et le compteur de résultats mentait à l'utilisateur.
    """
    from app.services.seed import seed_catalog

    await seed_catalog(session)
    total = (await auth_client.get("/api/furniture-types")).json()["total"]
    assert total > 1

    found = await auth_client.get("/api/furniture-types", params={"search": needle})

    assert found.json()["total"] < total, f"{needle!r} se comporte encore comme un joker"


# --- 404 plutôt que 500 sur un identifiant client -------------------------------------------------


async def test_a_share_pointing_at_a_vanished_project_answers_404(
    auth_client: AsyncClient, client: AsyncClient, session: AsyncSession
) -> None:
    """Régression : `scalar_one` sur un identifiant venu d'un partage remontait en 500."""
    plan = await _plan(auth_client)
    created = await auth_client.post(
        f"/api/projects/{plan['project']['id']}/shared-views",
        json={"state": {"camera_preset": "dessus"}},
    )
    token = created.json()["token"]

    # La ligne de partage survit à son projet, contrairement à ce que la cascade garantit :
    # on force le cas en supprimant le projet sans passer par l'API.
    from sqlalchemy import delete as sql_delete

    await session.execute(
        sql_delete(Face).where(
            col(Face.room_id).in_(
                select(col(Room.id)).where(col(Room.project_id) == plan["project"]["id"])
            )
        )
    )
    await session.execute(
        sql_delete(Room).where(col(Room.project_id) == plan["project"]["id"])
    )
    await session.execute(
        sql_delete(Project).where(col(Project.id) == plan["project"]["id"])
    )
    await session.commit()

    response = await client.get(f"/api/public/views/{token}")

    assert response.status_code == 404, response.text
