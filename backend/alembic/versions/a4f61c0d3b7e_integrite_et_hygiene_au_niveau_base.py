"""integrite et hygiene au niveau base

Revision ID: a4f61c0d3b7e
Revises: b0401c711d34
Create Date: 2026-08-07 10:12:44.517903

Migration écrite à la main, et volontairement en **un seul palier**. Chaque `ALTER TABLE` prend
un verrou exclusif sur PostgreSQL : sept tables retouchées en sept migrations, ce sont sept
fenêtres d'indisponibilité au lieu d'une.

Elle **commence par un contrôle des données existantes**. Poser les `CHECK` directement sur une
base qui contient déjà des lignes écrites par SQLAdmin, par la CLI ou par du SQL direct
échouerait au milieu du DDL, sur un message qui ne dit ni quelle table ni quelle ligne est
fautive. Ici la migration s'arrête avant d'avoir rien écrit et donne les identifiants à corriger.

Trois autres points méritent d'être signalés :

1. Tout passe par `op.batch_alter_table`. SQLite ne sait ni ajouter une contrainte, ni changer
   une valeur par défaut : le mode batch recrée la table. Sur PostgreSQL, il retombe sur des
   `ALTER TABLE` ordinaires.
2. `JSON` devient `JSONB` **sur PostgreSQL uniquement** (`postgresql_using` fait la conversion).
   Sur SQLite le type rendu est le même avant et après.
3. Le backfill de `sharedview` **ne purge pas** `state` des clés qu'il recopie : tant que le
   `downgrade` doit rester praticable, les retirer ferait perdre l'expiration de tous les liens
   au premier retour arrière — c'est-à-dire rouvrirait des partages volontairement fermés.
"""

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from alembic.operations.base import BatchOperations
from sqlalchemy.dialects import postgresql

revision: str = "a4f61c0d3b7e"
down_revision: str | None = "b0401c711d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bornes identiques à celles de `app/models/plan.py` et de `app/schemas/plan.py`. Elles sont
# recopiées et non importées : une migration doit décrire l'état du schéma au moment où elle a été
# écrite, pas suivre les modèles d'aujourd'hui.
CHECK_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("room", "ck_room_wall_thickness_cm_bounded",
     "wall_thickness_cm > 0 AND wall_thickness_cm <= 10000"),
    ("room", "ck_room_ceiling_height_cm_bounded",
     "ceiling_height_cm > 0 AND ceiling_height_cm <= 10000"),
    ("room", "ck_room_name_not_empty", "length(name) > 0"),
    ("element", "ck_element_width_cm_bounded", "width_cm > 0 AND width_cm <= 10000"),
    ("element", "ck_element_height_cm_bounded", "height_cm > 0 AND height_cm <= 10000"),
    ("element", "ck_element_depth_cm_bounded", "depth_cm > 0 AND depth_cm <= 10000"),
    ("element", "ck_element_rotation_deg_bounded", "rotation_deg >= -360 AND rotation_deg <= 360"),
    ("element", "ck_element_offsets_not_negative", "x_offset_cm >= 0 AND y_offset_cm >= 0"),
    ("furnituretype", "ck_furnituretype_default_width_cm_bounded",
     "default_width_cm > 0 AND default_width_cm <= 1000"),
    ("furnituretype", "ck_furnituretype_default_height_cm_bounded",
     "default_height_cm > 0 AND default_height_cm <= 1000"),
    ("furnituretype", "ck_furnituretype_default_depth_cm_bounded",
     "default_depth_cm > 0 AND default_depth_cm <= 1000"),
)

# (table, colonne, littéral SQL de la valeur par défaut) des sept colonnes JSON.
JSON_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("room", "polygon", "'[]'"),
    ("face", "covering", "'{}'"),
    ("furnituretype", "color_slots", "'[]'"),
    ("furnituretype", "parts", "'[]'"),
    ("element", "colors", "'{}'"),
    ("element", "variant_params", "'{}'"),
    ("sharedview", "state", "'{}'"),
)

TIMESTAMPED_TABLES = ("user", "project", "room", "face", "furnituretype", "element", "sharedview")

# Colonnes que ce palier ajoute à `sharedview`, dans l'ordre de création.
SHARED_VIEW_COLUMNS = (
    "expires_at",
    "revoked_at",
    "label",
    "public_label",
    "view_count",
    "last_viewed_at",
    "password_hash",
)

EXPIRY_STATE_KEY = "__expires_at"
LABEL_STATE_KEY = "label"
LABEL_MAX_LENGTH = 100

TIMESTAMP = sa.DateTime(timezone=True)


def _json_type() -> sa.JSON:
    """Une instance neuve à chaque appel : un type SQLAlchemy porte l'état de son rattachement."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _refuse_rows_the_new_constraints_would_reject(connection: sa.Connection) -> None:
    """Arrête la migration en listant les lignes fautives, avant toute écriture.

    Une `CHECK` posée à l'aveugle sur une base de production échoue au milieu du DDL, sur un
    message PostgreSQL qui nomme la contrainte mais aucune ligne. Reconstituer la requête à la
    main pendant une fenêtre de déploiement est exactement ce qu'on ne veut pas avoir à faire.
    """
    faulty: list[str] = []
    for table, name, expression in CHECK_CONSTRAINTS:
        # Interpolation sûre : `table` et `expression` viennent d'une constante de ce module.
        identifiers = connection.execute(
            sa.text(f"SELECT id FROM {table} WHERE NOT ({expression}) ORDER BY id LIMIT 20")
        ).scalars().all()
        if identifiers:
            faulty.append(f"  {table} / {name} : id {list(identifiers)}")

    if faulty:
        raise RuntimeError(
            "Des lignes existantes violent les contraintes que cette migration installe. "
            "Rien n'a été écrit ; corrigez ces lignes puis relancez la migration.\n"
            + "\n".join(faulty)
        )


def _set_timestamp_defaults(batch_op: BatchOperations, *, install: bool) -> None:
    for column in ("created_at", "updated_at"):
        batch_op.alter_column(
            column,
            existing_type=TIMESTAMP,
            existing_nullable=False,
            server_default=sa.func.now() if install else None,
        )


def _convert_json_column(batch_op: BatchOperations, column: str, empty: str) -> None:
    batch_op.alter_column(
        column,
        existing_type=sa.JSON(),
        type_=_json_type(),
        existing_nullable=False,
        server_default=sa.text(empty),
        postgresql_using=f"{column}::jsonb",
    )


def _revert_json_column(batch_op: BatchOperations, column: str) -> None:
    batch_op.alter_column(
        column,
        existing_type=_json_type(),
        type_=sa.JSON(),
        existing_nullable=False,
        server_default=None,
        postgresql_using=f"{column}::json",
    )


def _backfill_shared_views(connection: sa.Connection) -> None:
    """Sort l'expiration et le libellé de `state` vers leurs colonnes.

    Fait en Python et non en SQL : l'extraction d'une clé JSON ne s'écrit pas pareil sur
    PostgreSQL et sur SQLite, et surtout la valeur stockée est une chaîne ISO 8601 que seul
    `datetime.fromisoformat` sait relire correctement pour les deux moteurs.
    """
    update = sa.text(
        "UPDATE sharedview SET expires_at = :expires_at, label = :label WHERE id = :id"
    ).bindparams(sa.bindparam("expires_at", type_=TIMESTAMP))

    for row in connection.execute(sa.text("SELECT id, state FROM sharedview")).mappings():
        state = row["state"]
        if isinstance(state, str | bytes):
            state = json.loads(state)
        if not isinstance(state, dict):
            continue

        expires_at: datetime | None = None
        raw_expiry = state.get(EXPIRY_STATE_KEY)
        if isinstance(raw_expiry, str):
            try:
                expires_at = datetime.fromisoformat(raw_expiry)
            except ValueError:
                # Une date illisible n'est pas une raison d'interrompre un déploiement : le lien
                # reste servi, exactement comme avant la migration.
                expires_at = None

        raw_label = state.get(LABEL_STATE_KEY)
        # Tronqué à la largeur de la colonne : `state` a pu être écrit par SQLAdmin, qui ne
        # connaît pas la limite de 100 caractères appliquée par l'API.
        label = raw_label[:LABEL_MAX_LENGTH] if isinstance(raw_label, str) else None

        if expires_at is None and label is None:
            continue
        connection.execute(update, {"expires_at": expires_at, "label": label, "id": row["id"]})


def upgrade() -> None:
    connection = op.get_bind()
    _refuse_rows_the_new_constraints_would_reject(connection)

    with op.batch_alter_table("user") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        batch_op.alter_column(
            "is_active",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )
        batch_op.alter_column(
            "is_superuser",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )
        batch_op.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("email_verified_at", TIMESTAMP, nullable=True))

    with op.batch_alter_table("project") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        batch_op.alter_column(
            "version",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("1"),
        )
        # `ix_project_owner_id` est un préfixe strict de `ix_project_owner_updated`, qui sert donc
        # déjà le filtre par propriétaire seul ; `ix_project_name` n'est servi par aucune requête.
        # Deux index de plus à maintenir à chaque écriture, pour rien.
        batch_op.drop_index("ix_project_owner_id")
        batch_op.drop_index("ix_project_name")

    with op.batch_alter_table("room") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        _convert_json_column(batch_op, "polygon", "'[]'")

    with op.batch_alter_table("face") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        _convert_json_column(batch_op, "covering", "'{}'")

    with op.batch_alter_table("furnituretype") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        _convert_json_column(batch_op, "color_slots", "'[]'")
        _convert_json_column(batch_op, "parts", "'[]'")

    with op.batch_alter_table("element") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        _convert_json_column(batch_op, "colors", "'{}'")
        _convert_json_column(batch_op, "variant_params", "'{}'")

    with op.batch_alter_table("sharedview") as batch_op:
        _set_timestamp_defaults(batch_op, install=True)
        _convert_json_column(batch_op, "state", "'{}'")
        batch_op.add_column(sa.Column("expires_at", TIMESTAMP, nullable=True))
        batch_op.add_column(sa.Column("revoked_at", TIMESTAMP, nullable=True))
        batch_op.add_column(sa.Column("label", sa.String(length=LABEL_MAX_LENGTH), nullable=True))
        batch_op.add_column(
            sa.Column("public_label", sa.String(length=LABEL_MAX_LENGTH), nullable=True)
        )
        batch_op.add_column(
            sa.Column("view_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("last_viewed_at", TIMESTAMP, nullable=True))
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_sharedview_expires_at", ["expires_at"], unique=False)

    _backfill_shared_views(connection)

    # Les contraintes en dernier : le contrôle d'ouverture les a déjà validées, mais les poser
    # après le reste garde chaque table à une seule reconstruction en mode batch.
    for table in ("room", "element", "furnituretype"):
        with op.batch_alter_table(table) as batch_op:
            for constraint_table, name, expression in CHECK_CONSTRAINTS:
                if constraint_table == table:
                    batch_op.create_check_constraint(name, expression)


def downgrade() -> None:
    for table in ("room", "element", "furnituretype"):
        with op.batch_alter_table(table) as batch_op:
            for constraint_table, name, _expression in CHECK_CONSTRAINTS:
                if constraint_table == table:
                    batch_op.drop_constraint(name, type_="check")

    with op.batch_alter_table("sharedview") as batch_op:
        batch_op.drop_index("ix_sharedview_expires_at")
        for column in SHARED_VIEW_COLUMNS:
            batch_op.drop_column(column)
        _revert_json_column(batch_op, "state")
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("element") as batch_op:
        _revert_json_column(batch_op, "colors")
        _revert_json_column(batch_op, "variant_params")
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("furnituretype") as batch_op:
        _revert_json_column(batch_op, "color_slots")
        _revert_json_column(batch_op, "parts")
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("face") as batch_op:
        _revert_json_column(batch_op, "covering")
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("room") as batch_op:
        _revert_json_column(batch_op, "polygon")
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("project") as batch_op:
        batch_op.create_index("ix_project_name", ["name"], unique=False)
        batch_op.create_index("ix_project_owner_id", ["owner_id"], unique=False)
        batch_op.alter_column(
            "version", existing_type=sa.Integer(), existing_nullable=False, server_default=None
        )
        _set_timestamp_defaults(batch_op, install=False)

    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("email_verified_at")
        batch_op.drop_column("token_version")
        batch_op.alter_column(
            "is_superuser",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "is_active", existing_type=sa.Boolean(), existing_nullable=False, server_default=None
        )
        _set_timestamp_defaults(batch_op, install=False)
