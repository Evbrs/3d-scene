"""Ticket P8 — partage de vue par lien permalien (`docs/spec-complete.md` §3.5).

L'endpoint public sert des données sans authentification : les tests portent autant sur ce qu'il
expose que sur ce qu'il n'expose **pas**.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.share import EXPIRY_KEY, public_rate_limiter
from app.models.base import utcnow
from app.models.plan import SharedView

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]

STATE = {
    "camera_preset": "face-A",
    "visible_faces": ["A", "B"],
    "transparent_faces": ["C"],
    "camera_position": [100.0, 125.0, 240.0],
}


@pytest.fixture(autouse=True)
def _reset_public_limiter() -> None:
    public_rate_limiter.clear()


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
    assert body["project_name"] == "À partager"
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

    shared = (
        await session.execute(select(SharedView).where(col(SharedView.token) == token))
    ).scalar_one()
    shared.state = {**shared.state, EXPIRY_KEY: (utcnow() - timedelta(days=1)).isoformat()}
    await session.commit()

    expired = await client.get(f"/api/public/views/{token}")
    missing = await client.get("/api/public/views/" + "z" * 43)

    assert expired.status_code == missing.status_code == 404
    assert expired.json() == missing.json()


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
    assert EXPIRY_KEY not in (await client.get(f"/api/public/views/{token}")).text


async def test_deleting_the_project_removes_its_shares(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    project_id, token = await _shared_project(auth_client)

    await auth_client.delete(f"/api/projects/{project_id}")

    assert (await client.get(f"/api/public/views/{token}")).status_code == 404


async def test_the_public_endpoint_is_rate_limited(
    auth_client: AsyncClient, client: AsyncClient
) -> None:
    """Sans limite, l'endpoint public est un amplificateur : un calcul de scène par appel."""
    _project_id, token = await _shared_project(auth_client)

    statuses = []
    for _ in range(public_rate_limiter.max_attempts + 3):
        statuses.append((await client.get(f"/api/public/views/{token}")).status_code)

    assert 429 in statuses, "aucune limitation de débit sur l'endpoint public"


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
