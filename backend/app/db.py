"""Moteur SQLAlchemy et session de requête.

Le moteur est asynchrone (`asyncpg`-style via `psycopg` async) pour rester cohérent avec le
choix FastAPI async natif (`docs/spec-complete.md` §6.1). Un moteur synchrone séparé est exposé
pour SQLAdmin et Alembic, qui travaillent en synchrone.

Le pool est dimensionné explicitement (voir `app/core/config.py` pour l'arithmétique) : les
défauts de SQLAlchemy sont pensés pour un processus unique, alors que la production fait tourner
quatre workers uvicorn et deux processus Celery devant un PostgreSQL dont le `max_connections`
vaut 100.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _async_url(url: str) -> str:
    """Traduit une URL synchrone en URL asynchrone équivalente.

    Les URL de configuration sont écrites en synchrone (`postgresql+psycopg://`) parce que c'est
    la forme attendue par Alembic et par les outils externes ; `psycopg` (v3) sait fonctionner
    dans les deux modes avec le même driver.
    """
    if url.startswith("sqlite+aiosqlite://") or url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Moteur asynchrone partagé par l'application (créé à la première demande)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            _async_url(settings.database_url),
            echo=settings.sql_echo,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : une session par requête, refermée à la fin."""
    async with get_session_factory()() as session:
        yield session


async def reset_engine(*, close: bool = True) -> None:
    """Abandonne le moteur courant, après avoir rendu ses connexions.

    La version précédente se contentait de remettre la variable globale à `None`. Le pool restait
    alors vivant, référencé par personne, avec ses connexions PostgreSQL ouvertes : appelée depuis
    un chemin HTTP — ce qui était le cas de l'export direct — la fonction fuyait un pool complet
    par requête, jusqu'à épuisement du `max_connections` du serveur.

    `close=False` sert **après un fork** : les connexions héritées appartiennent au processus
    parent, et les fermer depuis l'enfant refermerait des sockets encore utilisées par le
    serveur web. Le pool est alors abandonné sans toucher aux descripteurs.
    """
    global _engine, _session_factory
    engine, _engine = _engine, None
    _session_factory = None
    if engine is not None:
        await engine.dispose(close=close)
