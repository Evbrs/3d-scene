"""Fixtures partagées.

La base de test est un fichier SQLite temporaire par défaut, pour que `cd backend && pytest`
reste exécutable sans Docker (`CLAUDE.md`, section Commandes). La CI rejoue la même suite contre
un vrai PostgreSQL via `TEST_DATABASE_URL`, pour que le choix d'un type portable (`JSON` plutôt
que `JSONB`) soit vérifié sur les deux moteurs.

Un *fichier* et non `:memory:` : SQLAdmin ouvre son propre moteur synchrone, et deux connexions
distinctes à une base SQLite en mémoire voient deux bases différentes.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="renovation-tests-"))
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DIR / 'test.db'}")

# Doit être positionné AVANT tout import applicatif : la configuration est mise en cache, et
# SQLAdmin construit son moteur au moment où l'application est créée.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# La configuration refuse volontairement de démarrer hors développement avec la clé par défaut
# (garde-fou anti-« oubli de SECRET_KEY en production »). Les tests s'annoncent donc comme tels.
os.environ.setdefault("ENVIRONMENT", "development")
# Celery en exécution immédiate : la suite tourne sans broker (P9).
os.environ.setdefault("CELERY_EAGER", "true")
os.environ.setdefault("EXPORT_DIR", str(_TMP_DIR / "exports"))

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel  # noqa: E402

import app.models  # noqa: E402, F401  — peuple SQLModel.metadata
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import get_session  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402

ADMIN_PASSWORD = "motdepasse-admin-de-test-2026"
USER_PASSWORD = "motdepasse-utilisateur-2026"


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Moteur de test, schéma créé et détruit autour de chaque test."""
    test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    if is_sqlite(TEST_DATABASE_URL):
        # SQLite n'applique PAS les clés étrangères par défaut : sans ce PRAGMA, le test qui
        # vérifie qu'une FK bloque un Element orphelin passerait pour de mauvaises raisons.
        @event.listens_for(test_engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    yield test_engine

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Client HTTP branché sur la session de test via l'override de dépendance FastAPI."""

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    fastapi_app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


async def _authenticated_client(client: AsyncClient, email: str) -> AsyncClient:
    """Client HTTP porteur d'un jeton d'accès pour `email` (compte créé au passage)."""
    registered = await client.post(
        "/api/auth/register", json={"email": email, "password": USER_PASSWORD}
    )
    assert registered.status_code == 202, registered.text
    tokens = await client.post(
        "/api/auth/token", data={"username": email, "password": USER_PASSWORD}
    )
    assert tokens.status_code == 200, tokens.text
    client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
    return client


@pytest.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Client authentifié — l'utilisateur « principal » des tests d'API."""
    return await _authenticated_client(client, "titulaire@exemple.fr")


@pytest.fixture
async def superuser_client(client: AsyncClient, session: AsyncSession) -> AsyncClient:
    """Client authentifié avec un compte superutilisateur (écriture du catalogue partagé)."""
    from sqlmodel import select as sqlmodel_select

    from app.core.security import hash_password

    session.add(
        User(
            email="catalogue@exemple.fr",
            hashed_password=hash_password(USER_PASSWORD),
            is_superuser=True,
        )
    )
    await session.commit()

    tokens = await client.post(
        "/api/auth/token", data={"username": "catalogue@exemple.fr", "password": USER_PASSWORD}
    )
    assert tokens.status_code == 200, tokens.text
    client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
    assert sqlmodel_select is not None
    return client


@pytest.fixture
async def other_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Second client authentifié, avec son propre compte.

    Transport distinct de `client` : partager l'instance écraserait l'en-tête d'autorisation du
    premier compte, et les tests de cloisonnement ne prouveraient plus rien.
    """

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    fastapi_app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield await _authenticated_client(ac, "tiers@exemple.fr")


@pytest.fixture(autouse=True)
def reset_login_rate_limiter() -> None:
    """Le limiteur de débit est un état de processus, partagé par tous les tests.

    Sans cette remise à zéro, un test qui crée quelques comptes épuise le quota par IP et fait
    échouer les suivants — un couplage invisible et pénible à diagnostiquer.
    """
    from app.api.auth import login_rate_limiter

    login_rate_limiter.clear()


@pytest.fixture
async def admin_client(client: AsyncClient, session: AsyncSession) -> AsyncClient:
    """Client authentifié sur le back-office avec un compte superutilisateur."""
    from app.core.security import hash_password

    admin = User(
        email="admin-test@exemple.fr",
        hashed_password=hash_password(ADMIN_PASSWORD),
        is_superuser=True,
    )
    session.add(admin)
    await session.commit()

    response = await client.post(
        "/admin/login", data={"username": "admin-test@exemple.fr", "password": ADMIN_PASSWORD}
    )
    assert response.status_code in (200, 302), response.text
    return client


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    """Propriétaire par défaut des projets de test (P2 : tout projet a un propriétaire)."""
    user = User(email="proprietaire-test@exemple.fr", hashed_password="argon2-factice")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def foreign_keys_enforced(session: AsyncSession) -> bool:
    """Vrai si le moteur de test applique réellement les contraintes de clé étrangère."""
    if not is_sqlite(TEST_DATABASE_URL):
        return True
    result = await session.execute(text("PRAGMA foreign_keys"))
    return bool(result.scalar())
