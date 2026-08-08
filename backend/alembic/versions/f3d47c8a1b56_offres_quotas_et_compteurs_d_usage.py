"""offres, quotas et compteurs d'usage

Revision ID: f3d47c8a1b56
Revises: f3a71c2d5b48
Create Date: 2026-08-08 21:12:44.903117

Migration écrite à la main. Quatre tables neuves, **une** colonne ajoutée à `project`.

Cinq points méritent d'être signalés.

1. `plan_catalog` est semé par la migration elle-même, avec la grille de
   `docs/strategie-produit.md` §4. Sans lignes, aucune organisation n'a de droits et le produit
   refuse tout : le catalogue n'est pas une donnée de démonstration, c'est une donnée de
   fonctionnement. Le semis n'a lieu qu'ici, sur une table qui vient d'être créée, donc il ne peut
   écraser aucune limite ajustée à la main ; `app/services/seed_plans.py` complète ensuite les
   paliers **absents** sans jamais toucher aux paliers présents.
2. Les valeurs semées ici sont **recopiées** depuis `app/services/seed_plans.py` et non importées.
   Une migration décrit l'état du monde au moment où elle a été écrite ; la faire suivre la grille
   d'aujourd'hui la rendrait irrejouable à la première remise commerciale.
3. `subscription.plan_code` est une clé étrangère **sans** `ondelete` : supprimer un palier auquel
   des clients sont abonnés doit échouer, pas les laisser silencieusement sans droits.
4. La contrainte d'unicité de `usage_counter` n'est pas de l'hygiène : c'est la cible du
   `INSERT … ON CONFLICT DO UPDATE` qui rend l'incrément atomique. La retirer ne casserait aucun
   test de forme et rouvrirait la course que ce lot existe pour fermer.
5. La colonne de `usage_event` s'appelle bien `metadata` — c'est sous ce nom qu'on la cherchera en
   incident — alors que l'attribut Python s'appelle `event_metadata` : `metadata` est réservé par
   la base déclarative SQLAlchemy sur toute classe mappée.

`project.archived_at` est **nullable et sans valeur par défaut** : toutes les lignes existantes
restent actives. Le déclassement n'est jamais rétroactif, et il ne supprime rien — un chantier
excédentaire devient lisible et non modifiable, pas absent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3d47c8a1b56"
# Chaînée derrière `f3a71c2d5b48` (jetons de compte) et non derrière `e7b3c05f1a62` : les deux
# lots ont été écrits en parallèle sur la même tête, ce qui ouvrait deux branches. Les deux
# révisions sont indépendantes — l'une crée `usertoken`, l'autre le catalogue d'offres — donc
# l'ordre choisi ne change rien au schéma obtenu, il rend seulement la ligne unique.
down_revision: str | None = "f3a71c2d5b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCRIPTION_STATUS_LABELS = ("trialing", "active", "past_due", "canceled")
SUBSCRIPTION_STATUS_ENUM_NAME = "subscriptionstatus"

TIMESTAMP = sa.DateTime(timezone=True)
# `JSONB` sur PostgreSQL, `JSON` textuel ailleurs — exactement `app/models/base.py::json_type`.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# La grille de `docs/strategie-produit.md` §4, figée au jour de cette migration. Prix en centimes
# entiers, hors taxes, par mois ; le tarif annuel est le prix mensuel équivalent en engagement
# annuel (deux mois offerts), et il est nul pour un palier « sur devis ».
_ALL_FEATURES = (
    "quotes",
    "exports_without_watermark",
    "dimensioned_elevations",
    "tiling_waste",
    "compliance_check",
    "multi_seat",
    "shared_price_book",
    "white_label",
    "client_signature",
    "priced_variants",
    "auto_layout",
    "api",
    "sso",
    "agency_stats",
)


def _features(*granted: str) -> dict[str, bool]:
    """Toutes les clés, celles non citées étant explicitement fausses.

    Explicite plutôt que par absence : la page tarifs a besoin de savoir qu'une fonctionnalité
    existe ailleurs pour pouvoir l'afficher dans la colonne « ce qui est bloqué ».
    """
    return {feature: feature in granted for feature in _ALL_FEATURES}


def _limits(
    active_projects: int | None,
    rooms_per_project: int | None,
    seats: int | None,
    share_link_days: int,
) -> dict[str, int | None]:
    """Limites d'un palier. `None` veut dire **illimité**, jamais zéro."""
    return {
        "active_projects": active_projects,
        "rooms_per_project": rooms_per_project,
        "seats": seats,
        "share_link_days": share_link_days,
        "exports_pdf": None,
        "quotes_issued": None,
        "ai_runs": None,
        "api_calls": None,
    }


PLAN_ROWS: tuple[dict[str, object], ...] = (
    {
        "code": "decouverte",
        "name": "Découverte",
        "tagline": "Essayer, et faire circuler des liens",
        "monthly_price_cents": 0,
        "yearly_price_cents": 0,
        "seat_price_cents": 0,
        "currency": "EUR",
        "limits": _limits(1, 2, 1, 30),
        "features": _features(),
        "is_public": True,
        "sort_order": 10,
    },
    {
        "code": "artisan",
        "name": "Artisan",
        "tagline": "Le solo, cœur de cible",
        "monthly_price_cents": 2900,
        "yearly_price_cents": 2400,
        "seat_price_cents": 0,
        "currency": "EUR",
        "limits": _limits(None, None, 1, 90),
        "features": _features(
            "quotes",
            "exports_without_watermark",
            "dimensioned_elevations",
            "tiling_waste",
            "compliance_check",
        ),
        "is_public": True,
        "sort_order": 20,
    },
    {
        "code": "entreprise",
        "name": "Entreprise",
        "tagline": "2 à 15 personnes",
        "monthly_price_cents": 7900,
        "yearly_price_cents": 6500,
        "seat_price_cents": 1900,
        "currency": "EUR",
        "limits": _limits(None, None, 15, 90),
        "features": _features(
            "quotes",
            "exports_without_watermark",
            "dimensioned_elevations",
            "tiling_waste",
            "compliance_check",
            "multi_seat",
            "shared_price_book",
            "white_label",
            "client_signature",
            "priced_variants",
            "auto_layout",
            "api",
        ),
        "is_public": True,
        "sort_order": 30,
    },
    {
        "code": "reseau",
        "name": "Réseau",
        "tagline": "Franchises, réseaux de cuisinistes, négoces",
        "monthly_price_cents": 39000,
        "yearly_price_cents": None,
        "seat_price_cents": 0,
        "currency": "EUR",
        "limits": _limits(None, None, None, 90),
        "features": _features(*_ALL_FEATURES),
        "is_public": True,
        "sort_order": 40,
    },
)


def _shared_enum(labels: tuple[str, ...], name: str) -> sa.Enum:
    """Type d'une colonne d'énumération, **sans** création implicite du type nommé.

    `create_type=False` n'existe que sur le dialecte PostgreSQL ; ailleurs, `sa.Enum` se rend en
    `VARCHAR` avec une contrainte `CHECK` et n'a aucun type à créer.
    """
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(*labels, name=name, create_type=False)
    return sa.Enum(*labels, name=name)


def upgrade() -> None:
    connection = op.get_bind()
    sa.Enum(*SUBSCRIPTION_STATUS_LABELS, name=SUBSCRIPTION_STATUS_ENUM_NAME).create(
        connection, checkfirst=True
    )

    plan_catalog = op.create_table(
        "plan_catalog",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("tagline", sa.String(length=200), server_default="", nullable=False),
        sa.Column("monthly_price_cents", sa.Integer(), nullable=False),
        sa.Column("yearly_price_cents", sa.Integer(), nullable=True),
        sa.Column("seat_price_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("limits", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("features", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        # `sa.true()` et non `sa.text("1")` : PostgreSQL refuse un entier comme défaut de colonne
        # booléenne.
        sa.Column("is_public", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("length(code) > 0", name="ck_plan_catalog_code_not_empty"),
        sa.CheckConstraint("length(name) > 0", name="ck_plan_catalog_name_not_empty"),
        sa.CheckConstraint(
            "monthly_price_cents >= 0", name="ck_plan_catalog_monthly_price_not_negative"
        ),
        sa.CheckConstraint(
            "yearly_price_cents IS NULL OR yearly_price_cents >= 0",
            name="ck_plan_catalog_yearly_price_not_negative",
        ),
        sa.CheckConstraint(
            "seat_price_cents >= 0", name="ck_plan_catalog_seat_price_not_negative"
        ),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "subscription",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=30), nullable=False),
        sa.Column(
            "status",
            _shared_enum(SUBSCRIPTION_STATUS_LABELS, SUBSCRIPTION_STATUS_ENUM_NAME),
            server_default=sa.text("'trialing'"),
            nullable=False,
        ),
        sa.Column("current_period_start", TIMESTAMP, nullable=False),
        sa.Column("current_period_end", TIMESTAMP, nullable=False),
        sa.Column("trial_ends_at", TIMESTAMP, nullable=True),
        sa.Column("cancel_at", TIMESTAMP, nullable=True),
        sa.Column("seats", sa.Integer(), server_default=sa.text("1"), nullable=False),
        # Vides tant qu'aucun prestataire de paiement n'est branché. Les ajouter le jour où la
        # table reçoit des webhooks serait une migration sur table chaude.
        sa.Column("external_customer_id", sa.String(length=120), nullable=True),
        sa.Column("external_subscription_id", sa.String(length=120), nullable=True),
        sa.CheckConstraint("seats >= 1", name="ck_subscription_seats_at_least_one"),
        sa.CheckConstraint(
            "current_period_end > current_period_start", name="ck_subscription_period_ordered"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_code"], ["plan_catalog.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_organization_id", "subscription", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_subscription_organization_status",
        "subscription",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "usage_counter",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("period_start", TIMESTAMP, nullable=False),
        sa.Column("value", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("value >= 0", name="ck_usage_counter_value_not_negative"),
        sa.CheckConstraint("length(metric) > 0", name="ck_usage_counter_metric_not_empty"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "metric",
            "period_start",
            name="uq_usage_counter_organization_metric_period",
        ),
    )
    op.create_index(
        "ix_usage_counter_organization_id", "usage_counter", ["organization_id"], unique=False
    )

    op.create_table(
        "usage_event",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("metadata", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_usage_event_quantity_not_negative"),
        sa.CheckConstraint("length(metric) > 0", name="ck_usage_event_metric_not_empty"),
        sa.CheckConstraint(
            "length(idempotency_key) > 0", name="ck_usage_event_idempotency_key_not_empty"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        # `SET NULL` : la consommation d'un compte supprimé reste comptée pour l'organisation qui
        # l'a payée, alors que le compte, lui, doit pouvoir disparaître (RGPD).
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_event_idempotency_key", "usage_event", ["idempotency_key"], unique=True
    )
    op.create_index(
        "ix_usage_event_organization_occurred",
        "usage_event",
        ["organization_id", "occurred_at"],
        unique=False,
    )

    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("archived_at", TIMESTAMP, nullable=True))

    op.bulk_insert(plan_catalog, [dict(row) for row in PLAN_ROWS])


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("archived_at")

    op.drop_index("ix_usage_event_organization_occurred", table_name="usage_event")
    op.drop_index("ix_usage_event_idempotency_key", table_name="usage_event")
    op.drop_table("usage_event")

    op.drop_index("ix_usage_counter_organization_id", table_name="usage_counter")
    op.drop_table("usage_counter")

    op.drop_index("ix_subscription_organization_status", table_name="subscription")
    op.drop_index("ix_subscription_organization_id", table_name="subscription")
    op.drop_table("subscription")

    op.drop_table("plan_catalog")

    # Sur PostgreSQL, un type ENUM survit à la suppression des tables qui l'utilisent : sans ce
    # retrait, `downgrade base` puis `upgrade head` échoue sur « type already exists ».
    sa.Enum(*SUBSCRIPTION_STATUS_LABELS, name=SUBSCRIPTION_STATUS_ENUM_NAME).drop(
        op.get_bind(), checkfirst=True
    )
