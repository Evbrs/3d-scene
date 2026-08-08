"""Critères d'acceptation du ticket P2 (auth JWT et permissions objet).

Référence : `docs/spec-complete.md` §6 (auth) et §7 (P2 : « comptes, propriété des projets »).
"""

from datetime import timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.auth import REFRESH_COOKIE_NAME, login_rate_limiter
from app.core.config import get_settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import Project, User
from app.models.base import utcnow
from tests.conftest import personal_organization

VALID_PASSWORD = "motdepasse-solide-2026"


async def _register(client: AsyncClient, email: str, password: str = VALID_PASSWORD) -> int:
    response = await client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 202, response.text
    return await _user_id(client, email, password)


async def _user_id(client: AsyncClient, email: str, password: str = VALID_PASSWORD) -> int:
    """Identifiant du compte, obtenu par le seul canal désormais disponible : la connexion.

    L'inscription ne renvoie plus l'utilisateur créé — elle répondrait différemment selon que
    l'adresse existe ou non, ce qui suffirait à énumérer les comptes.
    """
    tokens = await _login(client, email, password)
    profile = await client.get("/api/auth/me", headers=_auth(tokens["access_token"]))
    assert profile.status_code == 200, profile.text
    identifier = profile.json()["id"]
    assert isinstance(identifier, int)
    return identifier


async def _login(client: AsyncClient, email: str, password: str = VALID_PASSWORD) -> dict[str, str]:
    response = await client.post(
        "/api/auth/token", data={"username": email, "password": password}
    )
    assert response.status_code == 200, response.text
    tokens = response.json()
    assert isinstance(tokens, dict)
    return tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Hachage ----------------------------------------------------------------------------------


def test_password_hashing_is_salted_and_verifiable() -> None:
    first = hash_password(VALID_PASSWORD)
    second = hash_password(VALID_PASSWORD)

    assert first != second, "deux hachages du même mot de passe doivent différer (sel aléatoire)"
    assert VALID_PASSWORD not in first
    assert first.startswith("$argon2id$"), "Argon2id attendu (recommandation OWASP)"
    assert verify_password(VALID_PASSWORD, first)
    assert not verify_password("mauvais-mot-de-passe", first)


def test_verify_password_returns_false_on_a_corrupted_hash() -> None:
    """Un hachage illisible doit refuser l'authentification, pas provoquer une 500."""
    assert not verify_password(VALID_PASSWORD, "pas-un-hachage")


# --- Jetons -----------------------------------------------------------------------------------


def test_access_and_refresh_tokens_are_not_interchangeable() -> None:
    access = create_access_token(42)
    refresh = create_refresh_token(42)

    assert decode_token(access, "access") == "42"
    assert decode_token(refresh, "refresh") == "42"

    from app.core.security import InvalidTokenError

    with pytest.raises(InvalidTokenError):
        decode_token(refresh, "access")
    with pytest.raises(InvalidTokenError):
        decode_token(access, "refresh")


def test_a_token_signed_with_another_key_is_rejected() -> None:
    from app.core.security import InvalidTokenError

    forged = jwt.encode(
        {"sub": "42", "type": "access", "exp": utcnow() + timedelta(minutes=5)},
        "une-autre-cle-totalement-differente",
        algorithm=ALGORITHM,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(forged, "access")


def test_an_expired_token_is_rejected() -> None:
    from app.core.security import InvalidTokenError

    expired = jwt.encode(
        {"sub": "42", "type": "access", "exp": utcnow() - timedelta(seconds=1)},
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(expired, "access")


def test_the_none_algorithm_is_rejected() -> None:
    """`alg: none` est l'attaque classique sur les implémentations JWT permissives."""
    from app.core.security import InvalidTokenError

    unsigned = jwt.encode(
        {"sub": "42", "type": "access", "exp": utcnow() + timedelta(minutes=5)},
        key="",
        algorithm="none",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(unsigned, "access")


# --- Inscription et connexion -----------------------------------------------------------------


async def test_register_then_login_then_read_own_profile(client: AsyncClient) -> None:
    user_id = await _register(client, "alice@exemple.fr")
    tokens = await _login(client, "alice@exemple.fr")

    response = await client.get("/api/auth/me", headers=_auth(tokens["access_token"]))

    assert response.status_code == 200
    assert response.json() == {
        "id": user_id,
        "email": "alice@exemple.fr",
        "is_active": True,
        "is_superuser": False,
    }


async def test_the_password_is_never_returned_nor_stored_in_clear(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "bob@exemple.fr", "password": VALID_PASSWORD}
    )

    assert VALID_PASSWORD not in response.text
    assert "password" not in response.json()

    stored = (
        await session.execute(select(User).where(User.email == "bob@exemple.fr"))
    ).scalar_one()
    assert stored.hashed_password != VALID_PASSWORD
    assert verify_password(VALID_PASSWORD, stored.hashed_password)


async def test_a_short_password_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "court@exemple.fr", "password": "court"}
    )
    assert response.status_code == 422


async def test_an_invalid_email_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "pas-un-email", "password": VALID_PASSWORD}
    )
    assert response.status_code == 422


async def test_registering_twice_is_indistinguishable_from_a_first_registration(
    client: AsyncClient,
) -> None:
    """Le *code de statut* est un oracle aussi bavard que le message : il doit être identique."""
    first = await client.post(
        "/api/auth/register", json={"email": "doublon@exemple.fr", "password": VALID_PASSWORD}
    )
    second = await client.post(
        "/api/auth/register", json={"email": "doublon@exemple.fr", "password": "un-autre-mdp-12"}
    )

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert "doublon@exemple.fr" not in second.text


async def test_a_second_registration_does_not_overwrite_the_password(
    client: AsyncClient,
) -> None:
    """Avaler le conflit ne doit pas se transformer en prise de contrôle du compte."""
    await _register(client, "cible-reprise@exemple.fr")

    await client.post(
        "/api/auth/register",
        json={"email": "cible-reprise@exemple.fr", "password": "mot-de-passe-pirate-99"},
    )

    hijacked = await client.post(
        "/api/auth/token",
        data={"username": "cible-reprise@exemple.fr", "password": "mot-de-passe-pirate-99"},
    )
    assert hijacked.status_code == 401
    assert (await _login(client, "cible-reprise@exemple.fr")) is not None


async def test_email_is_case_insensitive(client: AsyncClient) -> None:
    """Sinon `Alice@ex.fr` et `alice@ex.fr` sont deux comptes, et l'un verrouille l'autre."""
    await _register(client, "Casse@Exemple.FR")

    connected = await client.post(
        "/api/auth/token", data={"username": "casse@exemple.fr", "password": VALID_PASSWORD}
    )
    assert connected.status_code == 200

    # Et la seconde inscription avec une autre casse ne crée pas de doublon.
    await client.post(
        "/api/auth/register", json={"email": "CASSE@exemple.fr", "password": "encore-un-mdp-12"}
    )
    still_mine = await client.post(
        "/api/auth/token", data={"username": "casse@exemple.fr", "password": VALID_PASSWORD}
    )
    assert still_mine.status_code == 200


async def test_login_with_a_wrong_password_is_refused(client: AsyncClient) -> None:
    await _register(client, "carol@exemple.fr")

    response = await client.post(
        "/api/auth/token", data={"username": "carol@exemple.fr", "password": "mauvais-mdp-xx"}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_login_on_an_unknown_account_gives_the_same_answer(client: AsyncClient) -> None:
    """Le message ne doit pas permettre de distinguer « compte inconnu » de « mauvais mdp »."""
    await _register(client, "dave@exemple.fr")

    unknown = await client.post(
        "/api/auth/token", data={"username": "inconnu@exemple.fr", "password": VALID_PASSWORD}
    )
    wrong_password = await client.post(
        "/api/auth/token", data={"username": "dave@exemple.fr", "password": "mauvais-mdp-xx"}
    )

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json() == wrong_password.json()


async def test_a_deactivated_account_cannot_log_in(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _register(client, "desactive@exemple.fr")
    user = (
        await session.execute(select(User).where(User.email == "desactive@exemple.fr"))
    ).scalar_one()
    user.is_active = False
    await session.commit()

    response = await client.post(
        "/api/auth/token", data={"username": "desactive@exemple.fr", "password": VALID_PASSWORD}
    )
    assert response.status_code == 401


async def test_login_is_rate_limited(client: AsyncClient) -> None:
    await _register(client, "cible@exemple.fr")

    statuses = []
    for _ in range(login_rate_limiter.per_target.max_attempts + 2):
        response = await client.post(
            "/api/auth/token", data={"username": "cible@exemple.fr", "password": "mauvais-xxxx"}
        )
        statuses.append(response.status_code)

    assert 429 in statuses, f"aucune limitation de débit déclenchée : {statuses}"


async def test_a_successful_login_does_not_unlock_attacks_on_other_accounts(
    client: AsyncClient,
) -> None:
    """Le contournement classique : intercaler un succès sur son propre compte pour vider le
    compteur, et reprendre l'attaque sur la victime. Le seau par IP doit y résister."""
    await _register(client, "victime@exemple.fr")
    await _register(client, "attaquant@exemple.fr")

    blocked = False
    for _ in range(12):
        for _ in range(9):
            response = await client.post(
                "/api/auth/token",
                data={"username": "victime@exemple.fr", "password": "essai-invalide"},
            )
            if response.status_code == 429:
                blocked = True
                break
        if blocked:
            break
        # Succès sur son propre compte : ne doit PAS libérer le quota visant la victime.
        await _login(client, "attaquant@exemple.fr")

    assert blocked, "l'alternance succès/échecs contourne entièrement la limitation de débit"


async def test_registration_is_also_rate_limited(client: AsyncClient) -> None:
    """Sinon l'inscription fournit une réserve inépuisable de comptes valides."""
    statuses = []
    for index in range(70):
        response = await client.post(
            "/api/auth/register",
            json={"email": f"masse{index}@exemple.fr", "password": VALID_PASSWORD},
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            break

    assert 429 in statuses, "l'inscription n'est pas limitée en débit"


# --- Protection des routes --------------------------------------------------------------------


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me", headers=_auth("ceci-nest-pas-un-jeton"))
    assert response.status_code == 401


async def test_me_rejects_a_token_for_a_deleted_user(client: AsyncClient) -> None:
    token = create_access_token(999_999)
    response = await client.get("/api/auth/me", headers=_auth(token))
    assert response.status_code == 401


async def test_a_refresh_token_is_not_accepted_as_an_access_token(client: AsyncClient) -> None:
    await _register(client, "erin@exemple.fr")
    tokens = await _login(client, "erin@exemple.fr")

    response = await client.get("/api/auth/me", headers=_auth(tokens["refresh_token"]))
    assert response.status_code == 401


async def test_refresh_returns_a_new_usable_pair(client: AsyncClient) -> None:
    await _register(client, "frank@exemple.fr")
    tokens = await _login(client, "frank@exemple.fr")

    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 200
    renewed = response.json()
    assert (
        await client.get("/api/auth/me", headers=_auth(renewed["access_token"]))
    ).status_code == 200


async def test_refresh_rejects_an_access_token(client: AsyncClient) -> None:
    await _register(client, "grace@exemple.fr")
    tokens = await _login(client, "grace@exemple.fr")

    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401


# --- Le jeton de rafraîchissement vit dans un cookie -------------------------------------------


def _refresh_cookie(response: object) -> str:
    """En-tête `Set-Cookie` portant le jeton de rafraîchissement.

    Lu sur l'en-tête brut et non sur le pot de cookies : c'est le seul moyen de vérifier les
    attributs (`HttpOnly`, `Path`, `SameSite`), que `httpx` n'expose pas.
    """
    headers = getattr(response, "headers", None)
    assert headers is not None
    return next(
        str(value)
        for key, value in headers.multi_items()
        if key.lower() == "set-cookie" and str(value).startswith(f"{REFRESH_COOKIE_NAME}=")
    )


async def test_logging_in_stores_the_refresh_token_in_an_http_only_cookie(
    client: AsyncClient,
) -> None:
    """Un jeton long lisible par du JavaScript est un jeton exfiltrable par une injection."""
    await _register(client, "cookie@exemple.fr")

    response = await client.post(
        "/api/auth/token", data={"username": "cookie@exemple.fr", "password": VALID_PASSWORD}
    )

    cookie = _refresh_cookie(response)
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("SameSite=Lax", "SameSite=lax")
    # Restreint aux routes d'authentification : le jeton n'accompagne aucun autre appel de l'API.
    assert "Path=/api/auth" in cookie
    assert client.cookies.get(REFRESH_COOKIE_NAME) is not None


async def test_refresh_works_from_the_cookie_alone(client: AsyncClient) -> None:
    """Le contrat que le frontend implémente : `POST /refresh` sans corps."""
    await _register(client, "sans-corps@exemple.fr")
    await _login(client, "sans-corps@exemple.fr")

    response = await client.post("/api/auth/refresh")

    assert response.status_code == 200, response.text
    renewed = response.json()
    assert (
        await client.get("/api/auth/me", headers=_auth(renewed["access_token"]))
    ).status_code == 200
    # Rotation : le cookie est reposé à chaque rafraîchissement.
    assert _refresh_cookie(response).startswith(f"{REFRESH_COOKIE_NAME}=")


async def test_refresh_without_a_cookie_nor_a_body_is_refused(client: AsyncClient) -> None:
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_logging_out_clears_the_cookie(client: AsyncClient) -> None:
    """Le cookie est `httpOnly` : sans cette route, le client ne peut pas se déconnecter."""
    await _register(client, "sortie@exemple.fr")
    await _login(client, "sortie@exemple.fr")
    assert client.cookies.get(REFRESH_COOKIE_NAME) is not None

    response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None
    assert (await client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.parametrize(
    ("route", "spied", "payload"),
    [
        ("/api/auth/register", "hash_password", {"json": {"email": "argon@exemple.fr",
                                                          "password": VALID_PASSWORD}}),
        ("/api/auth/token", "verify_password", {"data": {"username": "argon@exemple.fr",
                                                         "password": VALID_PASSWORD}}),
    ],
)
async def test_argon2_never_runs_on_the_event_loop(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    spied: str,
    payload: dict[str, object],
) -> None:
    """Argon2id est *conçu* pour être lent — c'est ce qui le rend sûr.

    Exécuté sur la boucle d'événements, il fige toutes les requêtes en cours pendant plusieurs
    dizaines de millisecondes. Inscription et connexion sont atteignables sans authentification,
    donc en volume : c'est un déni de service à coût nul pour l'attaquant.
    """
    import threading

    from app.core import security

    if spied == "verify_password":
        # La connexion a besoin d'un compte : on l'inscrit **avant** de poser l'espion, pour ne
        # mesurer que le fil sur lequel tourne la vérification.
        await _register(client, "argon@exemple.fr")

    original = getattr(security, spied)
    seen: list[str] = []

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(f"app.api.auth.{spied}", spy)
    response = await client.post(route, **payload)  # type: ignore[arg-type]

    assert response.status_code in (200, 202), response.text
    assert seen and threading.main_thread().name not in seen, (
        f"{spied} s'exécute sur {seen}, donc sur la boucle d'événements"
    )


async def test_outside_development_the_refresh_token_never_reaches_the_body(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """En production, seul le cookie porte le jeton — et il est marqué `Secure`."""
    await _register(client, "prod@exemple.fr")

    production = get_settings().model_copy(update={"environment": "production"})
    monkeypatch.setattr("app.api.auth.get_settings", lambda: production)
    response = await client.post(
        "/api/auth/token", data={"username": "prod@exemple.fr", "password": VALID_PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert response.json()["refresh_token"] is None
    assert "Secure" in _refresh_cookie(response)


# --- Permissions objet ------------------------------------------------------------------------


async def test_object_permissions_hide_another_users_project(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Le projet d'une autre organisation est introuvable, pas « interdit ».

    Répondre 403 confirmerait son existence, donc permettrait d'énumérer les identifiants des
    autres clients. Ce qui autorise est désormais l'appartenance et non `owner_id`.
    """
    from app.api.permissions import get_owned_project

    owner_id = await _register(client, "proprietaire@exemple.fr")
    intruder_id = await _register(client, "intrus@exemple.fr")

    owner = (await session.execute(select(User).where(User.id == owner_id))).scalar_one()
    intruder = (await session.execute(select(User).where(User.id == intruder_id))).scalar_one()

    project = Project(
        name="Projet privé",
        owner_id=owner_id,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.commit()

    assert (await get_owned_project(session, project.id or 0, owner)).id == project.id

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await get_owned_project(session, project.id or 0, intruder)
    assert excinfo.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await get_owned_project(session, 999_999, owner)
    assert missing.value.status_code == 404


async def test_object_permissions_walk_up_from_element_to_owner(
    client: AsyncClient, session: AsyncSession
) -> None:
    from fastapi import HTTPException

    from app.api.permissions import get_owned_element, get_owned_face, get_owned_room
    from app.models import Element, ElementKind, Face, FaceKind, Room

    owner_id = await _register(client, "arbre@exemple.fr")
    intruder_id = await _register(client, "voisin@exemple.fr")

    owner = (await session.execute(select(User).where(User.id == owner_id))).scalar_one()
    intruder = (await session.execute(select(User).where(User.id == intruder_id))).scalar_one()

    project = Project(
        name="Projet arborescent",
        owner_id=owner_id,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.flush()
    room = Room(project_id=project.id or 0, name="Séjour")
    session.add(room)
    await session.flush()
    face = Face(room_id=room.id or 0, label="A", kind=FaceKind.WALL)
    session.add(face)
    await session.flush()
    element = Element(face_id=face.id or 0, kind=ElementKind.WINDOW)
    session.add(element)
    await session.commit()

    assert (await get_owned_room(session, room.id or 0, owner)).id == room.id
    assert (await get_owned_face(session, face.id or 0, owner)).id == face.id
    assert (await get_owned_element(session, element.id or 0, owner)).id == element.id

    for loader, object_id in (
        (get_owned_room, room.id),
        (get_owned_face, face.id),
        (get_owned_element, element.id),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await loader(session, object_id or 0, intruder)
        assert excinfo.value.status_code == 404


async def test_deleting_a_user_removes_their_projects(
    client: AsyncClient, session: AsyncSession
) -> None:
    """RGPD : le droit à l'effacement suppose une suppression en cascade."""
    owner_id = await _register(client, "efface@exemple.fr")
    user = (await session.execute(select(User).where(User.id == owner_id))).scalar_one()
    session.add(
        Project(
            name="À supprimer",
            owner_id=owner_id,
            organization_id=(await personal_organization(session, user)).id or 0,
        )
    )
    await session.commit()
    await session.delete(user)
    await session.commit()

    remaining = (
        await session.execute(select(Project).where(Project.owner_id == owner_id))
    ).scalars().all()
    assert remaining == []
