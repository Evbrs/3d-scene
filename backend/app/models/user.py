"""Compte utilisateur.

`docs/spec-complete.md` §7 (P2) : « Comptes, propriété des projets », amendé en §10 (A1).

Un compte n'autorise plus rien par lui-même. Ce qui ouvre un projet, c'est une **appartenance
acceptée** à l'organisation qui le porte (`app/models/organization.py`, `app/api/permissions.py`).
`Project.owner_id` ne reste qu'une trace de création : le comparer à l'utilisateur courant pour
décider d'un accès rouvrirait le cloisonnement que la vague 2 a fermé.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, false, text, true
from sqlmodel import Field

from app.models.base import TimestampedModel, value_enum


class User(TimestampedModel, table=True):
    """Un compte. Le mot de passe n'est jamais stocké ni journalisé en clair."""

    __tablename__ = "user"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=320, unique=True, index=True)
    hashed_password: str = Field(max_length=255)
    # `true()` / `false()` plutôt que `text("1")` : PostgreSQL refuse un entier comme valeur par
    # défaut d'une colonne booléenne, et SQLite n'a pas de littéral `true`.
    is_active: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    is_superuser: bool = Field(default=False, sa_column_kwargs={"server_default": false()})

    # Compteur de révocation : l'incrémenter invalide d'un coup tous les jetons déjà émis pour ce
    # compte. Posé en même temps que le reste plutôt qu'au moment de son usage — ajouter une
    # colonne à `user`, table lue à chaque requête authentifiée, n'est à faire qu'une fois.
    token_version: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    email_verified_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )


class UserTokenPurpose(StrEnum):
    """Ce qu'un jeton hors session autorise, et rien d'autre.

    L'usage est porté par une colonne plutôt que par une table par besoin : un jeton de
    réinitialisation présenté à une future route de vérification d'adresse doit être refusé, et
    c'est la comparaison sur ce champ qui le refuse.
    """

    PASSWORD_RESET = "password_reset"


# Longueur d'un SHA-256 en hexadécimal, comme pour `Invitation.token_hash`. Même raisonnement :
# le jeton est un secret aléatoire de 256 bits, pas un mot de passe humain — il n'y a aucun
# dictionnaire à lui opposer, et un hachage rapide est le seul qui reste indexable.
TOKEN_HASH_LENGTH = 64


class UserToken(TimestampedModel, table=True):
    """Jeton à usage unique adressé à un compte (aujourd'hui : mot de passe oublié).

    **Seul le hachage est stocké.** Le jeton en clair n'existe qu'une fois, dans le message
    envoyé à l'utilisateur : une copie de la base ne permet donc pas de prendre la main sur les
    comptes qu'elle contient. C'est la même règle que `Invitation`, et pour la même raison.

    La ligne est conservée après usage (`consumed_at`) plutôt que supprimée : c'est elle qui
    interdit de rejouer le lien, et elle garde la trace de la reprise de contrôle d'un compte —
    exactement ce qu'on veut relire après un incident.
    """

    __tablename__ = "usertoken"
    __table_args__ = (
        CheckConstraint("length(token_hash) > 0", name="ck_usertoken_token_hash_not_empty"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    purpose: UserTokenPurpose = Field(  # type: ignore[call-overload]
        default=UserTokenPurpose.PASSWORD_RESET,
        sa_type=value_enum(UserTokenPurpose, "usertokenpurpose"),
        sa_column_kwargs={"server_default": text("'password_reset'")},
    )
    token_hash: str = Field(max_length=TOKEN_HASH_LENGTH, unique=True, index=True)

    expires_at: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
    consumed_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
