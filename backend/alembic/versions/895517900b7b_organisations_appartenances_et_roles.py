"""organisations, appartenances et roles

Revision ID: 895517900b7b
Revises: 75eba53422d6
Create Date: 2026-08-08 10:17:20.079494

Migration écrite à la main. Elle fait basculer le porteur des droits de l'utilisateur vers
l'organisation (`docs/strategie-produit.md` §6, point 1), ce qui impose un **rétro-remplissage** :
`project.organization_id` doit être `NOT NULL` à l'arrivée, alors qu'aucune organisation n'existe
au départ.

Le passage se fait donc en trois temps, et l'ordre n'est pas négociable :

1. colonne ajoutée **nullable** — sans quoi l'`ALTER TABLE` échoue sur la première ligne
   existante ;
2. une organisation personnelle par propriétaire distinct, son appartenance `owner` acceptée, et
   l'affectation de chaque projet ;
3. seulement alors, `NOT NULL`, la clé étrangère et l'échange d'index.

Deux points méritent d'être signalés :

- Le type ENUM `organizationrole` est **partagé** par `membership` et `invitation`. Il est créé
  une seule fois, explicitement : laissé à `create_table`, PostgreSQL recevrait deux `CREATE TYPE`
  pour le même nom et la seconde table échouerait.
- La règle de nommage de l'organisation personnelle est **recopiée** ici et non importée depuis
  `app/api/organizations.py`. Une migration doit décrire l'état du monde au moment où elle a été
  écrite ; si la règle applicative change demain, les lignes déjà migrées ne doivent pas bouger.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "895517900b7b"
down_revision: str | None = "75eba53422d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_LABELS = ("owner", "admin", "editor", "viewer")
ROLE_ENUM_NAME = "organizationrole"

TIMESTAMP = sa.DateTime(timezone=True)

# Clé étrangère nommée : sur SQLite, le mode batch reconstruit la table à partir de la réflexion,
# et une contrainte anonyme n'y est plus désignable.
PROJECT_FK = "fk_project_organization_id"

OLD_PROJECT_INDEX = "ix_project_owner_updated"
NEW_PROJECT_INDEX = "ix_project_organization_updated"

_UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
DEFAULT_SLUG_BASE = "espace"


def _role_column_type() -> sa.Enum:
    """Type de la colonne `role`, **sans** création implicite du type nommé.

    `create_type=False` n'existe que sur le dialecte PostgreSQL ; ailleurs, `sa.Enum` se rend en
    `VARCHAR` avec une contrainte `CHECK` et n'a aucun type à créer.
    """
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(*ROLE_LABELS, name=ROLE_ENUM_NAME, create_type=False)
    return sa.Enum(*ROLE_LABELS, name=ROLE_ENUM_NAME)


def _personal_identity(user_id: int, email: str | None) -> tuple[str, str]:
    """Nom et slug de l'organisation personnelle d'un compte.

    Le slug se termine par l'identifiant du compte : il est unique par construction, donc le
    rétro-remplissage n'a aucune collision à arbitrer au milieu d'un déploiement.
    """
    local_part = (email or "").split("@", 1)[0]
    base = _UNSLUGGABLE.sub("-", local_part.strip().lower()).strip("-") or DEFAULT_SLUG_BASE
    return base.replace("-", " ").title() or DEFAULT_SLUG_BASE, f"{base}-{user_id}"


def _backfill_personal_organizations(connection: sa.Connection) -> None:
    """Donne à chaque propriétaire de projet son organisation, et y range ses projets.

    Une organisation **par propriétaire distinct** et non par projet : deux chantiers du même
    artisan appartiennent à la même entreprise, et les séparer lui ferait découvrir le produit
    avec un locataire par plan.

    Les comptes sans aucun projet n'ont pas d'organisation à ce stade : la leur est créée à leur
    première écriture (`permissions.default_organization_id`). Leur en fabriquer une ici
    créerait des locataires vides pour des comptes qui n'ont peut-être jamais rien fait.
    """
    owner_ids = (
        connection.execute(sa.text("SELECT DISTINCT owner_id FROM project ORDER BY owner_id"))
        .scalars()
        .all()
    )
    if not owner_ids:
        return

    # `"user"` est un mot réservé de PostgreSQL : les guillemets ne sont pas décoratifs.
    emails = {
        row["id"]: row["email"]
        for row in connection.execute(
            sa.text('SELECT id, email FROM "user" WHERE id IN :ids').bindparams(
                sa.bindparam("ids", value=list(owner_ids), expanding=True)
            )
        ).mappings()
    }

    insert_organization = sa.text(
        "INSERT INTO organization (name, slug, created_at, updated_at) "
        "VALUES (:name, :slug, :now, :now)"
    ).bindparams(sa.bindparam("now", type_=TIMESTAMP))
    read_organization = sa.text("SELECT id FROM organization WHERE slug = :slug")
    insert_membership = sa.text(
        "INSERT INTO membership "
        "(user_id, organization_id, role, invited_at, accepted_at, created_at, updated_at) "
        "VALUES (:user_id, :organization_id, 'owner', :now, :now, :now, :now)"
    ).bindparams(sa.bindparam("now", type_=TIMESTAMP))
    attach_projects = sa.text(
        "UPDATE project SET organization_id = :organization_id WHERE owner_id = :user_id"
    )

    # Un horodatage Python et non `func.now()` : la valeur part comme paramètre lié, et une
    # fonction SQL n'est pas une valeur. Toutes les lignes rétro-remplies portent le même
    # instant, ce qui rend la migration identifiable d'un coup d'œil en base.
    now = datetime.now(UTC)
    for owner_id in owner_ids:
        name, slug = _personal_identity(owner_id, emails.get(owner_id))
        connection.execute(insert_organization, {"name": name, "slug": slug, "now": now})
        organization_id = connection.execute(read_organization, {"slug": slug}).scalar_one()
        connection.execute(
            insert_membership,
            {"user_id": owner_id, "organization_id": organization_id, "now": now},
        )
        connection.execute(
            attach_projects, {"organization_id": organization_id, "user_id": owner_id}
        )


def upgrade() -> None:
    connection = op.get_bind()

    # Créé avant les tables qui s'en servent, et une seule fois pour les deux. `checkfirst=True`
    # rend l'appel inopérant sur les moteurs sans type nommé (SQLite).
    sa.Enum(*ROLE_LABELS, name=ROLE_ENUM_NAME).create(connection, checkfirst=True)

    op.create_table(
        "organization",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("siret", sa.String(length=14), nullable=True),
        sa.Column("legal_form", sa.String(length=50), nullable=True),
        sa.Column("share_capital_cents", sa.Integer(), nullable=True),
        sa.Column("rcs", sa.String(length=100), nullable=True),
        sa.Column("address_line1", sa.String(length=200), nullable=True),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("vat_number", sa.String(length=20), nullable=True),
        sa.Column("decennial_insurer", sa.String(length=200), nullable=True),
        sa.Column("decennial_policy_number", sa.String(length=100), nullable=True),
        sa.Column("decennial_coverage_area", sa.String(length=200), nullable=True),
        sa.Column("billing_email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.CheckConstraint("length(name) > 0", name="ck_organization_name_not_empty"),
        sa.CheckConstraint("length(slug) > 0", name="ck_organization_slug_not_empty"),
        sa.CheckConstraint(
            "share_capital_cents IS NULL OR share_capital_cents >= 0",
            name="ck_organization_share_capital_not_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organization_slug", "organization", ["slug"], unique=True)

    op.create_table(
        "membership",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "role", _role_column_type(), server_default=sa.text("'viewer'"), nullable=False
        ),
        sa.Column("invited_at", TIMESTAMP, nullable=True),
        sa.Column("accepted_at", TIMESTAMP, nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_organization"),
    )
    op.create_index(
        "ix_membership_organization_id", "membership", ["organization_id"], unique=False
    )

    op.create_table(
        "invitation",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role", _role_column_type(), server_default=sa.text("'viewer'"), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.Column("accepted_at", TIMESTAMP, nullable=True),
        sa.CheckConstraint("length(email) > 0", name="ck_invitation_email_not_empty"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invitation_organization_id", "invitation", ["organization_id"], unique=False
    )
    op.create_index("ix_invitation_token_hash", "invitation", ["token_hash"], unique=True)

    # Temps 1 : la colonne naît nullable, sinon la première ligne existante fait échouer le DDL.
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))

    # Temps 2 : le rétro-remplissage.
    _backfill_personal_organizations(connection)

    # Temps 3 : la colonne devient obligatoire, et l'index suit le nouveau filtre de `GET
    # /api/projects`. Les deux dans le même palier : un index laissé sur `owner_id` ne servirait
    # plus aucune requête, et PostgreSQL le maintiendrait pourtant à chaque écriture.
    with op.batch_alter_table("project") as batch_op:
        batch_op.alter_column("organization_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            PROJECT_FK, "organization", ["organization_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.drop_index(OLD_PROJECT_INDEX)
        batch_op.create_index(NEW_PROJECT_INDEX, ["organization_id", "updated_at"], unique=False)


def downgrade() -> None:
    # `drop_column` emporte la clé étrangère : PostgreSQL supprime les contraintes qui dépendent
    # de la colonne, et le mode batch de SQLite reconstruit la table sans elle.
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_index(NEW_PROJECT_INDEX)
        batch_op.create_index(OLD_PROJECT_INDEX, ["owner_id", "updated_at"], unique=False)
        batch_op.drop_column("organization_id")

    op.drop_index("ix_invitation_token_hash", table_name="invitation")
    op.drop_index("ix_invitation_organization_id", table_name="invitation")
    op.drop_table("invitation")

    op.drop_index("ix_membership_organization_id", table_name="membership")
    op.drop_table("membership")

    op.drop_index("ix_organization_slug", table_name="organization")
    op.drop_table("organization")

    # Sur PostgreSQL, un type ENUM survit à la suppression des tables qui l'utilisent : sans ce
    # retrait, `downgrade base` puis `upgrade head` échoue sur « type already exists ».
    sa.Enum(*ROLE_LABELS, name=ROLE_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
