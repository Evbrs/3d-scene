"""mobilier libre et fond de plan

Revision ID: e7b3c05f1a62
Revises: 4c1e8b7a92d5
Create Date: 2026-08-08 17:40:02.118344

Migration écrite à la main, en **un seul palier** : deux tables retouchées, deux `ALTER TABLE`
groupés plutôt que sept, chacun prenant un verrou exclusif sur PostgreSQL.

Elle porte les amendements A4 et A5 de `docs/spec-complete.md` §10 :

- `element.face_id` devient **nullable** et `element.room_id` apparaît. Un élément s'ancre
  désormais à une face *ou* au sol d'une pièce, jamais aux deux ni à aucune — c'est
  `ck_element_exactly_one_anchor` qui le dit, en base et pas seulement dans Pydantic. Le
  rétro-remplissage est trivial : toute ligne existante a une face, donc satisfait déjà la
  branche « adossé » de la contrainte, et ses deux nouvelles colonnes de position restent nulles.
- `room` reçoit le calage du fond de plan. Les colonnes géométriques sont `NOT NULL` avec une
  valeur par défaut neutre (pas de translation, pas de rotation, opacité pleine) ; l'échelle, au
  contraire, est **nullable** et le reste tant que le calibrage n'a pas eu lieu.

Trois points méritent d'être signalés.

1. Tout passe par `op.batch_alter_table`. SQLite ne sait ni ajouter une contrainte, ni relâcher
   un `NOT NULL` : le mode batch recrée la table. Aucun `table_args` n'est passé — le dialecte
   SQLite reflète bien les `CHECK` posées par la révision `a4f61c0d3b7e`, et les redéclarer les
   ferait apparaître **deux fois** dans le `CREATE TABLE` reconstruit (vérifié). Sur PostgreSQL,
   le mode batch retombe sur des `ALTER TABLE` ordinaires.
2. Les expressions des `CHECK` sont **recopiées** et non importées des modèles. Une migration
   décrit l'état du schéma au moment où elle a été écrite ; la faire suivre les modèles
   d'aujourd'hui la rendrait irrejouable dès le prochain amendement.
3. Le `downgrade` refuse de s'exécuter s'il reste du mobilier libre. Remettre `face_id` en
   `NOT NULL` supprimerait ces lignes — un lit, une table, un îlot — sans que personne le
   demande. La migration s'arrête avant d'avoir rien écrit et donne les identifiants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3c05f1a62"
down_revision: str | None = "4c1e8b7a92d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_CENTIMETERS = 10_000
MAX_PLAN_COORDINATE = 100_000

# Exactement un des deux ancrages, coordonnées comprises : une ligne qui porterait à la fois un
# décalage de face et une position de pièce n'aurait pas de repère décidable.
ANCHOR_CHECK = (
    "(face_id IS NOT NULL AND room_id IS NULL "
    "AND pos_x_cm IS NULL AND pos_y_cm IS NULL) "
    "OR (face_id IS NULL AND room_id IS NOT NULL "
    "AND pos_x_cm IS NOT NULL AND pos_y_cm IS NOT NULL)"
)

NEW_ELEMENT_CHECKS: tuple[tuple[str, str], ...] = (
    ("ck_element_exactly_one_anchor", ANCHOR_CHECK),
    # Un percement flottant au milieu d'une pièce n'a aucun sens (spec §3.1). `kind` est stocké
    # sous sa valeur, d'où le littéral en minuscules.
    ("ck_element_opening_needs_a_face", "kind = 'furniture' OR face_id IS NOT NULL"),
    (
        "ck_element_position_bounded",
        f"(pos_x_cm IS NULL OR (pos_x_cm >= -{MAX_PLAN_COORDINATE} "
        f"AND pos_x_cm <= {MAX_PLAN_COORDINATE})) "
        f"AND (pos_y_cm IS NULL OR (pos_y_cm >= -{MAX_PLAN_COORDINATE} "
        f"AND pos_y_cm <= {MAX_PLAN_COORDINATE}))",
    ),
)

NEW_ROOM_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_room_background_scale_bounded",
        "background_scale_cm_per_px IS NULL OR (background_scale_cm_per_px > 0 "
        f"AND background_scale_cm_per_px <= {MAX_CENTIMETERS})",
    ),
    ("ck_room_background_opacity_bounded", "background_opacity >= 0 AND background_opacity <= 1"),
    (
        "ck_room_background_rotation_deg_bounded",
        "background_rotation_deg >= -360 AND background_rotation_deg <= 360",
    ),
    (
        "ck_room_background_offsets_bounded",
        f"background_offset_x_cm >= -{MAX_PLAN_COORDINATE} "
        f"AND background_offset_x_cm <= {MAX_PLAN_COORDINATE} "
        f"AND background_offset_y_cm >= -{MAX_PLAN_COORDINATE} "
        f"AND background_offset_y_cm <= {MAX_PLAN_COORDINATE}",
    ),
)

# Colonnes ajoutées à `room`, dans l'ordre de création.
ROOM_BACKGROUND_COLUMNS = (
    "background_url",
    "background_scale_cm_per_px",
    "background_offset_x_cm",
    "background_offset_y_cm",
    "background_rotation_deg",
    "background_opacity",
)


def upgrade() -> None:
    with op.batch_alter_table("element") as batch_op:
        batch_op.add_column(sa.Column("room_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("pos_x_cm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("pos_y_cm", sa.Float(), nullable=True))
        batch_op.alter_column("face_id", existing_type=sa.Integer(), nullable=True)
        batch_op.create_index("ix_element_room_id", ["room_id"])
        batch_op.create_foreign_key(
            "fk_element_room_id_room", "room", ["room_id"], ["id"], ondelete="CASCADE"
        )
        for name, expression in NEW_ELEMENT_CHECKS:
            batch_op.create_check_constraint(name, expression)

    with op.batch_alter_table("room") as batch_op:
        batch_op.add_column(sa.Column("background_url", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("background_scale_cm_per_px", sa.Float(), nullable=True)
        )
        for column in ("background_offset_x_cm", "background_offset_y_cm"):
            batch_op.add_column(
                sa.Column(column, sa.Float(), nullable=False, server_default=sa.text("0"))
            )
        batch_op.add_column(
            sa.Column(
                "background_rotation_deg",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "background_opacity", sa.Float(), nullable=False, server_default=sa.text("1")
            )
        )
        for name, expression in NEW_ROOM_CHECKS:
            batch_op.create_check_constraint(name, expression)


def _refuse_to_lose_free_furniture(connection: sa.Connection) -> None:
    """Arrête le retour arrière plutôt que de supprimer du mobilier libre.

    `face_id` redevient `NOT NULL` : les lignes ancrées à une pièce n'ont aucune valeur à y
    mettre. Les supprimer serait une perte de données silencieuse — un lit, une table, un îlot
    disparus d'un plan pour une raison d'exploitation. La migration s'arrête avant d'avoir rien
    écrit et donne les identifiants à traiter.
    """
    identifiers = (
        connection.execute(
            sa.text("SELECT id FROM element WHERE face_id IS NULL ORDER BY id LIMIT 20")
        )
        .scalars()
        .all()
    )
    if identifiers:
        raise RuntimeError(
            "Des éléments sont posés au sol d'une pièce et n'ont pas de face : ce retour arrière "
            "les supprimerait. Rien n'a été écrit ; rattachez-les ou supprimez-les "
            f"explicitement, puis relancez. id : {list(identifiers)}"
        )


def downgrade() -> None:
    _refuse_to_lose_free_furniture(op.get_bind())

    with op.batch_alter_table("room") as batch_op:
        for name, _expression in NEW_ROOM_CHECKS:
            batch_op.drop_constraint(name, type_="check")
        for column in reversed(ROOM_BACKGROUND_COLUMNS):
            batch_op.drop_column(column)

    with op.batch_alter_table("element") as batch_op:
        for name, _expression in NEW_ELEMENT_CHECKS:
            batch_op.drop_constraint(name, type_="check")
        batch_op.drop_constraint("fk_element_room_id_room", type_="foreignkey")
        batch_op.drop_index("ix_element_room_id")
        batch_op.alter_column("face_id", existing_type=sa.Integer(), nullable=False)
        for column in ("pos_y_cm", "pos_x_cm", "room_id"):
            batch_op.drop_column(column)
