"""Environnement Alembic.

Les migrations tournent en synchrone : c'est le mode natif d'Alembic, et ça évite d'imposer une
boucle asyncio à un outil de ligne de commande. L'URL vient de la configuration applicative,
jamais de `alembic.ini`.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Import nécessaire : il peuple `SQLModel.metadata` avec toutes les tables.
import app.models  # noqa: F401
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _database_url() -> str:
    """URL synchrone : Alembic n'utilise pas le moteur async de l'application."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (mode `--sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applique les migrations sur une connexion réelle."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Indispensable sur SQLite : sans ça, toute migration modifiant une contrainte
            # échoue faute d'ALTER TABLE complet.
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
