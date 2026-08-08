"""Compte utilisateur.

`docs/spec-complete.md` §7 (P2) : « Comptes, propriété des projets », amendé en §10 (A1).

Un compte n'autorise plus rien par lui-même. Ce qui ouvre un projet, c'est une **appartenance
acceptée** à l'organisation qui le porte (`app/models/organization.py`, `app/api/permissions.py`).
`Project.owner_id` ne reste qu'une trace de création : le comparer à l'utilisateur courant pour
décider d'un accès rouvrirait le cloisonnement que la vague 2 a fermé.
"""

from datetime import datetime

from sqlalchemy import DateTime, false, text, true
from sqlmodel import Field

from app.models.base import TimestampedModel


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
