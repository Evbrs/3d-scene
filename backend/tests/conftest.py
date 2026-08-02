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


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Moteur de test, schéma créé et détruit autour de chaque test."""
    test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    if _is_sqlite(TEST_DATABASE_URL):
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


@pytest.fixture
async def foreign_keys_enforced(session: AsyncSession) -> bool:
    """Vrai si le moteur de test applique réellement les contraintes de clé étrangère."""
    if not _is_sqlite(TEST_DATABASE_URL):
        return True
    result = await session.execute(text("PRAGMA foreign_keys"))
    return bool(result.scalar())
