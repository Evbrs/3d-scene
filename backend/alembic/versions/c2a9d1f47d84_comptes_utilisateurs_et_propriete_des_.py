"""comptes utilisateurs et propriete des projets

Revision ID: c2a9d1f47d84
Revises: 8de2811b136b
Create Date: 2026-08-02 18:41:12.933366

Migration retouchée à la main par rapport à l'autogénération, sur trois points :

1. `owner_id` ne peut pas être ajoutée directement en `NOT NULL` sur une table qui contient déjà
   des lignes. On ajoute la colonne nullable, on rattache les projets orphelins au plus ancien
   compte, puis on passe la colonne en `NOT NULL`.
2. S'il existe des projets orphelins et aucun compte pour les recevoir, la migration s'arrête
   avec un message explicite plutôt que d'échouer sur une violation de contrainte illisible.
3. Les contraintes sont nommées : `op.drop_constraint(None, ...)` généré pour le `downgrade`
   échouerait à l'exécution.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "c2a9d1f47d84"
down_revision: str | None = "8de2811b136b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_project_owner_id_user"


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=False),
        sa.Column(
            "hashed_password", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)

    op.add_column("project", sa.Column("owner_id", sa.Integer(), nullable=True))

    connection = op.get_bind()
    orphan_count = connection.execute(
        sa.text("SELECT count(*) FROM project WHERE owner_id IS NULL")
    ).scalar_one()

    if orphan_count:
        fallback_owner_id = connection.execute(
            sa.text("SELECT id FROM \"user\" ORDER BY id LIMIT 1")
        ).scalar_one_or_none()
        if fallback_owner_id is None:
            raise RuntimeError(
                f"{orphan_count} projet(s) sans propriétaire et aucun compte existant. "
                "Créez un compte avant d'appliquer cette migration, ou supprimez ces projets."
            )
        connection.execute(
            sa.text("UPDATE project SET owner_id = :owner WHERE owner_id IS NULL"),
            {"owner": fallback_owner_id},
        )

    with op.batch_alter_table("project") as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(op.f("ix_project_owner_id"), ["owner_id"], unique=False)
        batch_op.create_foreign_key(FK_NAME, "user", ["owner_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.drop_index(op.f("ix_project_owner_id"))
        batch_op.drop_column("owner_id")

    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_table("user")
