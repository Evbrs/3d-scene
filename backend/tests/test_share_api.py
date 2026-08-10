"""Ticket P8 — partage de vue par lien permalien (`docs/spec-complete.md` §3.5).

L'endpoint public sert des données sans authentification : les tests portent autant sur ce qu'il
expose que sur ce qu'il n'expose **pas**.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.share import LEGACY_EXPIRY_KEY
from app.core.rate_limit import COSTLY_QUOTAS, rate_limiter
from app.models.base import utcnow
from app.models.plan import SharedView

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]

STATE = {
    "camera_preset": "face-A",
    "visible_faces": ["A", "B"],
    "transparent_faces": ["C"],
    "camera_position": [100.0, 125.0, 240.0],
}


async def _shared_view(session: AsyncSession, token: str) -> SharedView:
    return (
        await session.execute(select(SharedView).where(col(SharedView.token) == token))
    ).scalar_one()


@pytest.fixture(autouse=True)
def _reset_public_limiter() -> None:
    """Le compteur de débit est partagé par toute l'application, donc par tous les tests."""
    rate_limiter.clear()


async def _shared_project(client: AsyncClient) -> tuple[int, str]:
    project = (await client.post("/api/projects", json={"name": "À partager"})).json()
    await client.post(
        f"/api/projects/{project['id']}/rooms",
        json={"name": "Salon", "polygon": CARRE},
    )
    created = await client.post(
        f"/api/projects/{project['id']}/shared-views", json={"state": STATE}
    )
    assert created.status_code == 201, created.text
    return project["id"], created.json()["token"]


# --- Création et révocation -----------------------------------------------------------------


async def test_the_owner_can_create_and_revoke_a_share(auth_client: AsyncClient) -> None:
    project_id, token = await _shared_project(auth_client)

    listed = await auth_client.get(f"/api/projects/{project_id}/shared-views")
    assert listed.status_code == 200
    assert [entry["token"] for entry in listed.json()] == [token]

    shared_id = listed.json()[0]["id"]
    assert (await auth_client.delete(f"/api/shared-views/{shared_id}")).status_code == 204
    assert (await auth_client.get(f"/api/public/views/{token}")).status_code == 404


async def test_the_token_is_unpredictable(auth_client: AsyncClient) -> None:
    """Un identifiant séquentiel rendrait tous les projets partagés énumérables."""
    tokens = {(await _shared_project(auth_client))[1] for _ in range(5)}

    assert len(tokens) == 5
    for token in tokens:
        assert len(token) >= 40, "jeton trop court pour être imprévisible"
        assert not token.isdigit()


async def test_another_account_cannot_share_or_revoke(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project_id, _token = await _shared_project(auth_client)

    assert (
        await other_client.post(
            f"/api/projects/{project_id}/shared-views", json={"state": STATE}
        )
    ).status_code == 404
    assert (
        await other_client.get(f"/api/projects/{project_id}/shared-views")
    ).status_code == 404

    shared_id = (await auth_client.get(f"/api/projects/{project_id}/shared-views")).json()[0]["id"]
    assert (await other_client.delete(f"/api/shared-views/{shared_id}")).status_code == 404


async def test_creating_a_share_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/projects/1/shared-views", json={"state": STATE})
    assert response.status_code == 401


# --- Lecture publique ------------------------------------------------------------------------


async def test_the_public_view_is_readable_without_any_token(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    _project_id, token = await _shared_project(auth_client)

    # Le client public ne porte aucun en-tête d'autorisation.
    anonymous = AsyncClient(transport=client._transport, base_url="http://test")
    response = await anonymous.get(f"/api/public/views/{token}")
    await anonymous.aclose()

    assert response.status_code == 200, response.text
    body = response.json()
    # Sans `public_label`, le titre est neutre : le nom brut du projet ne sort jamais.
    assert body["project_name"] == "Vue partagée"
    assert "À partager" not in response.text
    assert body["state"]["camera_preset"] == "face-A"
    assert len(body["scene"]["rooms"][0]["nodes"]) == 6


async def test_the_public_response_leaks_no_owner_information(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Un lien de partage ne doit rien révéler de plus que la vue elle-même."""
    _project_id, token = await _shared_project(auth_client)

    response = await client.get(f"/api/public/views/{token}")
    body = response.json()
    raw = response.text

    assert "owner" not in raw
    assert "titulaire@exemple.fr" not in raw
    assert "email" not in raw
    assert "project_id" not in body["scene"], "l'identifiant interne n'a rien à faire ici"
    assert "version" not in raw
    assert "updated_at" not in raw


async def test_an_unknown_token_is_a_plain_404(client: AsyncClient) -> None:
    response = await client.get("/api/public/views/" + "z" * 43)
    assert response.status_code == 404


async def test_a_too_short_token_is_refused_before_any_lookup(client: AsyncClient) -> None:
    assert (await client.get("/api/public/views/court")).status_code == 422


async def test_an_expired_share_is_indistinguishable_from_a_missing_one(
    auth_client: AsyncClient, client: AsyncClient, session: AsyncSession
) -> None:
    """Distinguer les deux cas confirmerait qu'un lien a existé."""
    _project_id, token = await _shared_project(auth_client)

    shared = await _shared_view(session, token)
    shared.expires_at = utcnow() - timedelta(days=1)
    await session.commit()

    expired = await client.get(f"/api/public/views/{token}")
    missing = await client.get("/api/public/views/" + "z" * 43)

    assert expired.status_code == missing.status_code == 404
    assert expired.json() == missing.json()


async def test_the_expiry_lives_in_a_column_and_not_in_the_json_blob(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Régression : l'expiration était rangée dans `state`, donc ni indexable ni contrôlable.

    Elle y avait surtout **deux** sources de vérité une fois la colonne posée par le modèle : une
    écriture pouvait rouvrir un partage volontairement fermé.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Daté"})).json()
    created = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": STATE, "expires_in_days": 7},
    )

    assert created.status_code == 201, created.text
    assert created.json()["expires_at"] is not None
    shared = await _shared_view(session, created.json()["token"])
    assert shared.expires_at is not None
    assert LEGACY_EXPIRY_KEY not in shared.state


@pytest.mark.parametrize("stored", ["pas-une-date", "", 12345, None])
async def test_an_unreadable_legacy_expiry_closes_the_link(
    auth_client: AsyncClient, client: AsyncClient, session: AsyncSession, stored: object
) -> None:
    """Sur un endpoint public, un doute ferme l'accès.

    Les lignes antérieures à la colonne `expires_at` portent encore leur date dans `state`. Une
    valeur que la migration n'a pas su convertir était jusqu'ici purement ignorée — le lien
    restait donc ouvert pour toujours, alors que son propriétaire l'avait borné.
    """
    _project_id, token = await _shared_project(auth_client)

    shared = await _shared_view(session, token)
    shared.state = {**shared.state, LEGACY_EXPIRY_KEY: stored}
    await session.commit()

    assert (await client.get(f"/api/public/views/{token}")).status_code == 404


async def test_a_share_with_an_expiry_still_works_before_it(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Temporaire"})).json()
    await auth_client.post(f"/api/projects/{project['id']}/rooms",
                           json={"name": "P", "polygon": CARRE})
    created = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": STATE, "expires_in_days": 7},
    )

    token = created.json()["token"]
    assert (await client.get(f"/api/public/views/{token}")).status_code == 200
    # La date d'expiration est un détail interne : elle ne fuite pas dans la réponse publique.
    assert "expires_at" not in (await client.get(f"/api/public/views/{token}")).text


async def test_revoking_keeps_the_row_and_closes_the_link(
    auth_client: AsyncClient, client: AsyncClient, session: AsyncSession
) -> None:
    """La révocation ne doit pas effacer la preuve que le lien a existé.

    Supprimer la ligne libère aussi le jeton : rien n'empêche plus qu'il soit réattribué, et le
    propriétaire n'a aucune trace du partage qu'il vient de fermer.
    """
    project_id, token = await _shared_project(auth_client)
    shared_id = (await auth_client.get(f"/api/projects/{project_id}/shared-views")).json()[0]["id"]

    assert (await auth_client.delete(f"/api/shared-views/{shared_id}")).status_code == 204

    assert (await client.get(f"/api/public/views/{token}")).status_code == 404
    listed = (await auth_client.get(f"/api/projects/{project_id}/shared-views")).json()
    assert [entry["id"] for entry in listed] == [shared_id]
    assert listed[0]["revoked_at"] is not None
    assert (await _shared_view(session, token)).revoked_at is not None


async def test_deleting_the_project_removes_its_shares(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    project_id, token = await _shared_project(auth_client)

    await auth_client.delete(f"/api/projects/{project_id}")

    assert (await client.get(f"/api/public/views/{token}")).status_code == 404


async def test_the_public_endpoint_is_rate_limited(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Sans limite, l'endpoint public est un amplificateur : un calcul de scène par appel.

    Le plafond vient du compteur **partagé** et non plus d'un seau propre au processus. Le moteur
    lui-même est vérifié dans `tests/test_debit.py` : figer ici un compteur local reviendrait à
    protéger le défaut au lieu de le signaler.
    """
    _project_id, token = await _shared_project(auth_client)

    statuses = []
    for _ in range(COSTLY_QUOTAS["public_view"].max_events + 3):
        statuses.append((await client.get(f"/api/public/views/{token}")).status_code)

    assert 429 in statuses, "aucune limitation de débit sur l'endpoint public"


# --- Portée de ce qui est réellement publié -----------------------------------------------------


async def _project_with_three_rooms(client: AsyncClient) -> str:
    """Projet de trois pièces, partagé sur la deuxième."""
    project = (await client.post("/api/projects", json={"name": "Rénovation Dupont"})).json()
    for index, name in enumerate(("Salon", "Salle de bain", "Chambre")):
        offset = index * 500
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": name, "polygon": [[x + offset, y] for x, y in CARRE]},
        )
    created = await client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": {"camera_preset": "dessus", "room_index": 1}},
    )
    assert created.status_code == 201, created.text
    token: str = created.json()["token"]
    return token


async def test_the_public_view_only_serves_the_room_it_targets(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Régression : partager une pièce publiait le logement entier.

    L'état ne vise qu'une pièce, mais la réponse contenait le graphe complet — surfaces,
    revêtements, mobilier et dimensions de toutes les autres pièces, servis sans authentification
    à quiconque possède le lien.
    """
    token = await _project_with_three_rooms(auth_client)

    body = (await client.get(f"/api/public/views/{token}")).json()

    assert [room["name"] for room in body["scene"]["rooms"]] == ["Salle de bain"]
    assert "Chambre" not in (await client.get(f"/api/public/views/{token}")).text


async def test_a_share_pointing_at_a_deleted_room_serves_nothing(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Se rabattre sur les autres pièces serait exactement la fuite qu'on ferme."""
    token = await _project_with_three_rooms(auth_client)
    project_id = (await auth_client.get("/api/projects")).json()["items"][0]["id"]
    rooms = (await auth_client.get(f"/api/projects/{project_id}")).json()["rooms"]
    for room in rooms[1:]:
        assert (await auth_client.delete(f"/api/rooms/{room['id']}")).status_code == 204

    body = (await client.get(f"/api/public/views/{token}")).json()

    assert body["scene"]["rooms"] == []


async def test_a_public_label_replaces_the_project_name(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Le nom d'un projet de rénovation porte souvent un nom de client et une adresse."""
    created = await auth_client.post(
        "/api/projects", json={"name": "Rénovation Dupont, 12 rue des Lilas"}
    )
    project = created.json()
    await auth_client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "P", "polygon": CARRE}
    )
    created = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": STATE, "public_label": "Projet de salle de bain"},
    )

    response = await client.get(f"/api/public/views/{created.json()['token']}")

    assert response.json()["public_label"] == "Projet de salle de bain"
    assert response.json()["project_name"] == "Projet de salle de bain"
    assert "Dupont" not in response.text


@pytest.mark.parametrize("public_label", ["<script>alert(1)</script>", 'guillemet"', "a" * 101])
async def test_a_public_label_is_as_hardened_as_the_rest_of_the_state(
    auth_client: AsyncClient, public_label: str
) -> None:
    project = (await auth_client.post("/api/projects", json={"name": "Libellé public"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": STATE, "public_label": public_label},
    )
    assert response.status_code == 422, response.text


# --- Validation de l'état partagé ---------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    ["<img src=x onerror=alert(1)>", 'guillemet"', "et&commercial", "retour\nligne"],
)
async def test_a_label_with_markup_is_refused(auth_client: AsyncClient, label: str) -> None:
    """Régression : `label` échappait au durcissement appliqué au reste de `state`.

    Il est écrit par un client, stocké, puis restitué par un endpoint public sans
    authentification : c'est exactement ce que le plan interdit pour `state`.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Libellé"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views", json={"state": STATE, "label": label}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "label", ["Vue d'ensemble — salon", "Étage 1 (rénové)", "Salle de bain"]
)
async def test_a_normal_french_label_is_accepted(auth_client: AsyncClient, label: str) -> None:
    """Le durcissement ne doit pas refuser un libellé légitime : accents, tirets, apostrophes."""
    project = (await auth_client.post("/api/projects", json={"name": "Libellé"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views", json={"state": STATE, "label": label}
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "state",
    [
        {"camera_preset": "face-A", "champ_inconnu": 1},
        {"camera_preset": ""},
        {"camera_preset": "a" * 60},
        {"camera_preset": "<script>"},
        {"camera_preset": "face-A", "visible_faces": ["x" * 40]},
    ],
)
async def test_an_invalid_view_state_is_refused(
    auth_client: AsyncClient, state: dict[str, object]
) -> None:
    """`state` est écrit par un client et relu par un endpoint public : il doit rester borné."""
    project = (await auth_client.post("/api/projects", json={"name": "Validation"})).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views", json={"state": state}
    )
    assert response.status_code == 422, response.text
