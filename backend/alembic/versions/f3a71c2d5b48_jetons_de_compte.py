"""jetons de compte (mot de passe oublie)

Revision ID: f3a71c2d5b48
Revises: e7b3c05f1a62
Create Date: 2026-08-08 21:05:11.402117

Migration écrite à la main. Elle crée `usertoken`, la table qui rend un mot de passe oublié
récupérable : jusqu'ici aucune route de réinitialisation n'existait, SQLAdmin exclut le mot de
passe de son formulaire et la CLI ne l'expose pas — un mot de passe perdu était un compte et des
chantiers perdus définitivement.

Trois points méritent d'être signalés.

1. **La colonne stocke le hachage du jeton, jamais le jeton.** C'est la règle déjà appliquée à
   `invitation.token_hash`, et pour la même raison : une copie de la base ne doit pas permettre de
   prendre la main sur les comptes qu'elle contient. L'index unique sert les deux besoins —
   retrouver la ligne, et interdire deux jetons identiques.
2. **Le type ENUM `usertokenpurpose` est créé explicitement** sur PostgreSQL. Laissé à
   `create_table`, il serait recréé à chaque table qui l'emploierait ; le déclarer ici garde la
   main sur son cycle de vie, comme `organizationrole` l'a fait à la révision `895517900b7b`.
3. **Le `downgrade` ne perd rien d'irremplaçable.** Un jeton de réinitialisation vaut une heure :
   supprimer la table annule au pire des demandes en cours, que l'utilisateur refera. C'est la
   seule table du dépôt dont on peut dire ça, et c'est pour ça qu'il n'y a pas de garde-fou ici.

Aucune colonne n'est ajoutée à `user` : `token_version`, sur lequel s'appuie la révocation globale
des sessions, existe depuis la révision `a4f61c0d3b7e`. Seule son exploitation était manquante,
et elle est entièrement applicative (`app/core/security.py`, `app/api/deps.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a71c2d5b48"
down_revision: str | None = "e7b3c05f1a62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)

PURPOSE_LABELS = ("password_reset",)
PURPOSE_ENUM_NAME = "usertokenpurpose"

# Longueur d'un SHA-256 en hexadécimal, recopiée et non importée : une migration décrit l'état du
# schéma au moment où elle a été écrite.
TOKEN_HASH_LENGTH = 64


def _purpose_column_type(*, create_type: bool) -> sa.Enum:
    """Type de la colonne `purpose`, avec ou sans création implicite du type nommé.

    `create_type=False` n'existe que sur le dialecte PostgreSQL ; ailleurs, `sa.Enum` se rend en
    `VARCHAR` assorti d'une contrainte `CHECK` et n'a aucun type à créer.
    """
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(*PURPOSE_LABELS, name=PURPOSE_ENUM_NAME, create_type=create_type)
    return sa.Enum(*PURPOSE_LABELS, name=PURPOSE_ENUM_NAME)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _purpose_column_type(create_type=True).create(bind, checkfirst=True)

    op.create_table(
        "usertoken",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "purpose",
            _purpose_column_type(create_type=False),
            nullable=False,
            server_default="password_reset",
        ),
        sa.Column("token_hash", sa.String(length=TOKEN_HASH_LENGTH), nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.Column("consumed_at", TIMESTAMP, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(token_hash) > 0", name="ck_usertoken_token_hash_not_empty"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_usertoken_user_id_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usertoken"),
    )
    op.create_index("ix_usertoken_user_id", "usertoken", ["user_id"])
    op.create_index("ix_usertoken_token_hash", "usertoken", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_usertoken_token_hash", table_name="usertoken")
    op.drop_index("ix_usertoken_user_id", table_name="usertoken")
    op.drop_table("usertoken")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _purpose_column_type(create_type=False).drop(bind, checkfirst=True)
