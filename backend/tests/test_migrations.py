"""Critère d'acceptation A1 du ticket P1 : `alembic upgrade head` sur une base vide.

Le test tourne sur SQLite par défaut (exécutable sans Docker) **et** sur PostgreSQL quand
`TEST_DATABASE_URL` en fournit un — dans ce cas sur un schéma dédié, pour ne pas marcher sur les
tables de la suite principale.

Faire réellement tourner ces tests sur PostgreSQL n'est pas cosmétique : certains défauts de
réversibilité n'existent que là. Les types ENUM nommés survivent à `DROP TABLE`, si bien qu'un
`downgrade base` peut « réussir » tout en rendant l'`upgrade` suivant impossible.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import app.models  # noqa: F401  — peuple SQLModel.metadata
from tests.conftest import TEST_DATABASE_URL, is_sqlite

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_SCHEMA = "migrations_test"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "")


def _reset_migration_schema(drop_only: bool = False) -> None:
    engine = create_engine(_sync_url(TEST_DATABASE_URL), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{MIGRATION_SCHEMA}" CASCADE'))
            if not drop_only:
                conn.execute(text(f'CREATE SCHEMA "{MIGRATION_SCHEMA}"'))
    finally:
        engine.dispose()


@pytest.fixture
def empty_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """URL synchrone vers une base vierge, isolée de celle des autres tests."""
    if is_sqlite(TEST_DATABASE_URL):
        tmp_dir = Path(tempfile.mkdtemp(prefix="renovation-migrations-"))
        url = f"sqlite:///{tmp_dir / 'migrations.db'}"
    else:
        _reset_migration_schema()
        # Pas de `%` dans l'URL : Alembic la lit via configparser, qui l'interpréterait comme une
        # syntaxe d'interpolation et échouerait.
        url = f"{_sync_url(TEST_DATABASE_URL)}?options=-csearch_path={MIGRATION_SCHEMA}"

    # env.py lit l'URL depuis la configuration applicative, pas depuis alembic.ini.
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()

    if not is_sqlite(TEST_DATABASE_URL):
        _reset_migration_schema(drop_only=True)


def _table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        if is_sqlite(database_url):
            return set(inspector.get_table_names())
        return set(inspector.get_table_names(schema=MIGRATION_SCHEMA))
    finally:
        engine.dispose()


def test_upgrade_head_creates_every_table_on_an_empty_database(empty_database_url: str) -> None:
    command.upgrade(_alembic_config(empty_database_url), "head")

    created = _table_names(empty_database_url)
    expected = set(SQLModel.metadata.tables)
    assert expected <= created, f"tables manquantes après migration : {expected - created}"
    assert "alembic_version" in created


def test_upgrade_head_creates_every_column_of_every_model(empty_database_url: str) -> None:
    """Comparer les noms de tables ne suffit pas.

    Une migration qui oublie une colonne (par exemple `project.owner_id`) laisse le jeu de
    tables inchangé : le test passerait alors que le schéma est faux. On compare donc les
    colonnes, table par table, avec les modèles.
    """
    command.upgrade(_alembic_config(empty_database_url), "head")

    engine = create_engine(empty_database_url)
    try:
        inspector = inspect(engine)
        schema = None if is_sqlite(empty_database_url) else MIGRATION_SCHEMA
        for table_name, table in SQLModel.metadata.tables.items():
            actual = {
                column["name"] for column in inspector.get_columns(table_name, schema=schema)
            }
            expected = {column.name for column in table.columns}
            assert expected <= actual, (
                f"colonnes manquantes sur {table_name} : {sorted(expected - actual)}"
            )
    finally:
        engine.dispose()


def test_downgrade_base_removes_every_table(empty_database_url: str) -> None:
    """Une migration non réversible est une impasse opérationnelle : on vérifie l'aller-retour."""
    config = _alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    remaining = _table_names(empty_database_url)
    assert remaining <= {"alembic_version"}, f"tables résiduelles : {remaining}"


def test_the_migration_can_be_replayed_after_a_full_downgrade(empty_database_url: str) -> None:
    """Le vrai test de réversibilité : rejouer l'`upgrade` après un `downgrade` complet.

    Un `downgrade` peut supprimer toutes les *tables* et laisser derrière lui des objets qui
    feront échouer la migration suivante. Seul le rejeu le met en évidence.
    """
    config = _alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert set(SQLModel.metadata.tables) <= _table_names(empty_database_url)


@pytest.mark.skipif(
    is_sqlite(TEST_DATABASE_URL), reason="les types ENUM nommés n'existent que sur PostgreSQL"
)
def test_downgrade_also_removes_the_enum_types(empty_database_url: str) -> None:
    config = _alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(_sync_url(TEST_DATABASE_URL))
    try:
        with engine.connect() as conn:
            leftovers = (
                conn.execute(
                    text(
                        "SELECT t.typname FROM pg_type t "
                        "JOIN pg_namespace n ON n.oid = t.typnamespace "
                        "WHERE n.nspname = :schema AND t.typname IN ('facekind', 'elementkind')"
                    ),
                    {"schema": MIGRATION_SCHEMA},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert leftovers == [], f"types ENUM non supprimés par le downgrade : {leftovers}"
