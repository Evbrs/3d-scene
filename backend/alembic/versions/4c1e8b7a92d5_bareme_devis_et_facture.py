"""bareme de prix, devis et facture

Revision ID: 4c1e8b7a92d5
Revises: 895517900b7b
Create Date: 2026-08-08 14:05:11.402113

Migration écrite à la main. Six tables neuves, aucune table existante modifiée : le devis se
rattache au plan par des identifiants, jamais par des colonnes ajoutées ailleurs.

Trois points méritent d'être signalés.

- Le type ENUM `priceunit` est **partagé** par `price_item` et `quote_line`. Il est créé une seule
  fois, explicitement : laissé à `create_table`, PostgreSQL recevrait deux `CREATE TYPE` pour le
  même nom et la seconde table échouerait. Même leçon que `organizationrole` à la révision
  précédente.
- `quote.project_id` est en `ON DELETE SET NULL` là où tout le reste du dépôt est en `CASCADE`.
  Ce n'est pas une distraction : un devis émis est un contrat et une facture se conserve dix ans.
  Supprimer le plan du chantier ne doit pas effacer la comptabilité qui en découle, d'où la copie
  de `project_name` sur le document.
- `quote_counter` est une table et non une séquence PostgreSQL. Une séquence avance même sur
  transaction annulée : elle laisserait des trous définitifs dans une numérotation que la loi veut
  continue.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4c1e8b7a92d5"
down_revision: str | None = "895517900b7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE_UNIT_LABELS = ("m2", "ml", "u", "forfait")
PRICE_UNIT_ENUM_NAME = "priceunit"
QUOTE_STATUS_LABELS = ("draft", "sent", "accepted", "refused", "invoiced")
QUOTE_STATUS_ENUM_NAME = "quotestatus"
DOCUMENT_SERIES_LABELS = ("quote", "invoice")
DOCUMENT_SERIES_ENUM_NAME = "documentseries"

TIMESTAMP = sa.DateTime(timezone=True)
# `JSONB` sur PostgreSQL, `JSON` textuel ailleurs — exactement `app/models/base.py::json_type`.
# Créer la colonne en `sa.JSON()` simple ferait dériver le schéma : la révision
# `a4f61c0d3b7e` a converti toutes les colonnes JSON du dépôt en `JSONB`, et `alembic check`
# le détecte.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
# Quantité au millième : c'est la précision du métré (des m² au décimètre carré). Un flottant y
# réintroduirait le bruit binaire que tout le module de chiffrage cherche à éviter.
QUANTITY = sa.Numeric(precision=12, scale=3)


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

    # Créés avant les tables qui s'en servent, et une seule fois chacun. `checkfirst=True` rend
    # l'appel inopérant sur les moteurs sans type nommé (SQLite).
    for labels, name in (
        (PRICE_UNIT_LABELS, PRICE_UNIT_ENUM_NAME),
        (QUOTE_STATUS_LABELS, QUOTE_STATUS_ENUM_NAME),
        (DOCUMENT_SERIES_LABELS, DOCUMENT_SERIES_ENUM_NAME),
    ):
        sa.Enum(*labels, name=name).create(connection, checkfirst=True)

    op.create_table(
        "price_book",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        # `sa.false()` et non `sa.text("0")` : PostgreSQL refuse un entier comme défaut de
        # colonne booléenne, et SQLite rend bien « 0 ».
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("length(name) > 0", name="ck_price_book_name_not_empty"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_price_book_organization_name"
        ),
    )
    op.create_index(
        "ix_price_book_organization_id", "price_book", ["organization_id"], unique=False
    )

    op.create_table(
        "price_item",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("price_book_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column(
            "unit",
            _shared_enum(PRICE_UNIT_LABELS, PRICE_UNIT_ENUM_NAME),
            server_default=sa.text("'m2'"),
            nullable=False,
        ),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("vat_rate_bp", sa.Integer(), server_default=sa.text("2000"), nullable=False),
        sa.CheckConstraint("length(code) > 0", name="ck_price_item_code_not_empty"),
        sa.CheckConstraint("length(label) > 0", name="ck_price_item_label_not_empty"),
        sa.CheckConstraint(
            "unit_price_cents >= 0", name="ck_price_item_unit_price_not_negative"
        ),
        sa.CheckConstraint(
            "vat_rate_bp >= 0 AND vat_rate_bp <= 10000", name="ck_price_item_vat_rate_bounded"
        ),
        sa.ForeignKeyConstraint(["price_book_id"], ["price_book.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("price_book_id", "code", name="uq_price_item_book_code"),
    )
    op.create_index(
        "ix_price_item_price_book_id", "price_item", ["price_book_id"], unique=False
    )

    op.create_table(
        "face_costing",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("face_id", sa.Integer(), nullable=False),
        sa.Column("price_item_code", sa.String(length=40), nullable=True),
        sa.Column("override_quantity", QUANTITY, nullable=True),
        sa.Column("override_unit_price_cents", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "override_quantity IS NULL OR override_quantity >= 0",
            name="ck_face_costing_override_quantity_not_negative",
        ),
        sa.ForeignKeyConstraint(["face_id"], ["face.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("face_id", name="uq_face_costing_face"),
    )
    op.create_index("ix_face_costing_face_id", "face_costing", ["face_id"], unique=False)

    op.create_table(
        "quote",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("project_name", sa.String(length=200), nullable=True),
        sa.Column("number", sa.String(length=40), nullable=True),
        sa.Column(
            "status",
            _shared_enum(QUOTE_STATUS_LABELS, QUOTE_STATUS_ENUM_NAME),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("issued_at", TIMESTAMP, nullable=True),
        sa.Column("valid_until", TIMESTAMP, nullable=True),
        sa.Column("invoice_number", sa.String(length=40), nullable=True),
        sa.Column("invoiced_at", TIMESTAMP, nullable=True),
        sa.Column("due_date", TIMESTAMP, nullable=True),
        sa.Column("total_ht_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_tva_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_ttc_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("client_email", sa.String(length=320), nullable=True),
        sa.Column("client_phone", sa.String(length=30), nullable=True),
        sa.Column("client_address_line1", sa.String(length=200), nullable=True),
        sa.Column("client_address_line2", sa.String(length=200), nullable=True),
        sa.Column("client_postal_code", sa.String(length=20), nullable=True),
        sa.Column("client_city", sa.String(length=100), nullable=True),
        sa.Column("client_country", sa.String(length=100), nullable=True),
        sa.Column("client_vat_number", sa.String(length=20), nullable=True),
        sa.Column(
            "client_is_consumer", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("site_address_line1", sa.String(length=200), nullable=True),
        sa.Column("site_address_line2", sa.String(length=200), nullable=True),
        sa.Column("site_postal_code", sa.String(length=20), nullable=True),
        sa.Column("site_city", sa.String(length=100), nullable=True),
        sa.Column(
            "vat_attestation_required", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("vat_attestation_over_two_years", sa.Boolean(), nullable=True),
        sa.Column("vat_attestation_premises_use", sa.String(length=100), nullable=True),
        sa.Column("vat_attestation_signatory", sa.String(length=200), nullable=True),
        sa.Column("vat_attestation_signed_at", TIMESTAMP, nullable=True),
        sa.Column("payment_terms", sa.String(length=500), nullable=True),
        sa.Column(
            "late_penalty_rate_bp", sa.Integer(), server_default=sa.text("1050"), nullable=False
        ),
        sa.Column(
            "recovery_indemnity_cents",
            sa.Integer(),
            server_default=sa.text("4000"),
            nullable=False,
        ),
        sa.Column("mediator_name", sa.String(length=200), nullable=True),
        sa.Column("mediator_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("warnings", JSON_TYPE, server_default=sa.text("'[]'"), nullable=False),
        sa.CheckConstraint("length(client_name) > 0", name="ck_quote_client_name_not_empty"),
        sa.CheckConstraint(
            "late_penalty_rate_bp >= 0 AND late_penalty_rate_bp <= 10000",
            name="ck_quote_late_penalty_rate_bounded",
        ),
        sa.CheckConstraint(
            "recovery_indemnity_cents >= 0", name="ck_quote_recovery_indemnity_not_negative"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        # `SET NULL` : voir l'en-tête du fichier. Un document émis survit à la suppression du plan.
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "number", name="uq_quote_organization_number"),
        sa.UniqueConstraint(
            "organization_id", "invoice_number", name="uq_quote_organization_invoice_number"
        ),
    )
    op.create_index("ix_quote_organization_id", "quote", ["organization_id"], unique=False)
    op.create_index("ix_quote_project_id", "quote", ["project_id"], unique=False)

    op.create_table(
        "quote_line",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column(
            "unit",
            _shared_enum(PRICE_UNIT_LABELS, PRICE_UNIT_ENUM_NAME),
            server_default=sa.text("'m2'"),
            nullable=False,
        ),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("vat_rate_bp", sa.Integer(), server_default=sa.text("2000"), nullable=False),
        sa.Column("total_ht_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Traces d'origine **sans** clé étrangère : la ligne facturée doit survivre à la
        # suppression de la face et du barème dont elle est issue.
        sa.Column("source_face_id", sa.Integer(), nullable=True),
        sa.Column("source_price_item_code", sa.String(length=40), nullable=True),
        sa.CheckConstraint("length(label) > 0", name="ck_quote_line_label_not_empty"),
        sa.CheckConstraint("position >= 0", name="ck_quote_line_position_not_negative"),
        sa.CheckConstraint("quantity >= 0", name="ck_quote_line_quantity_not_negative"),
        sa.CheckConstraint(
            "vat_rate_bp >= 0 AND vat_rate_bp <= 10000", name="ck_quote_line_vat_rate_bounded"
        ),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id", "position", name="uq_quote_line_quote_position"),
    )
    op.create_index("ix_quote_line_quote_id", "quote_line", ["quote_id"], unique=False)

    op.create_table(
        "quote_counter",
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "series",
            _shared_enum(DOCUMENT_SERIES_LABELS, DOCUMENT_SERIES_ENUM_NAME),
            server_default=sa.text("'quote'"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("next_value", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("next_value >= 0", name="ck_quote_counter_next_value_not_negative"),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "series", "year", name="uq_quote_counter_organization_series_year"
        ),
    )
    op.create_index(
        "ix_quote_counter_organization_id", "quote_counter", ["organization_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_quote_counter_organization_id", table_name="quote_counter")
    op.drop_table("quote_counter")

    op.drop_index("ix_quote_line_quote_id", table_name="quote_line")
    op.drop_table("quote_line")

    op.drop_index("ix_quote_project_id", table_name="quote")
    op.drop_index("ix_quote_organization_id", table_name="quote")
    op.drop_table("quote")

    op.drop_index("ix_face_costing_face_id", table_name="face_costing")
    op.drop_table("face_costing")

    op.drop_index("ix_price_item_price_book_id", table_name="price_item")
    op.drop_table("price_item")

    op.drop_index("ix_price_book_organization_id", table_name="price_book")
    op.drop_table("price_book")

    # Sur PostgreSQL, un type ENUM survit à la suppression des tables qui l'utilisent : sans ce
    # retrait, `downgrade base` puis `upgrade head` échoue sur « type already exists ».
    connection = op.get_bind()
    for labels, name in (
        (PRICE_UNIT_LABELS, PRICE_UNIT_ENUM_NAME),
        (QUOTE_STATUS_LABELS, QUOTE_STATUS_ENUM_NAME),
        (DOCUMENT_SERIES_LABELS, DOCUMENT_SERIES_ENUM_NAME),
    ):
        sa.Enum(*labels, name=name).drop(connection, checkfirst=True)
