"""Critère d'acceptation A1 du ticket P1 : `alembic upgrade head` sur une base vide.

Le test tourne sur une base SQLite jetable pour rester exécutable sans Docker. La CI rejoue le
même test contre PostgreSQL via `TEST_DATABASE_URL`, et le job `compose` applique la migration
sur la vraie base.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.models  # noqa: F401  — peuple SQLModel.metadata

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def empty_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """URL synchrone vers une base vierge, isolée de celle des autres tests."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="renovation-migrations-"))
    url = f"sqlite:///{tmp_dir / 'migrations.db'}"
    # env.py lit l'URL depuis la configuration applicative, pas depuis alembic.ini.
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_upgrade_head_creates_every_table_on_an_empty_database(empty_database_url: str) -> None:
    command.upgrade(_alembic_config(empty_database_url), "head")

    engine = create_engine(empty_database_url)
    try:
        created = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = set(SQLModel.metadata.tables)
    assert expected <= created, f"tables manquantes après migration : {expected - created}"
    assert "alembic_version" in created


def test_downgrade_base_removes_every_table(empty_database_url: str) -> None:
    """Une migration non réversible est une impasse opérationnelle : on vérifie l'aller-retour."""
    config = _alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(empty_database_url)
    try:
        remaining = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert remaining <= {"alembic_version"}, f"tables résiduelles : {remaining}"
