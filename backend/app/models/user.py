"""Compte utilisateur.

`docs/spec-complete.md` §7 (P2) : « Comptes, propriété des projets ». La propriété est portée
par `Project.owner_id`, et les permissions objet en découlent (`app/api/deps.py`).
"""

from sqlmodel import Field

from app.models.base import TimestampedModel


class User(TimestampedModel, table=True):
    """Un compte. Le mot de passe n'est jamais stocké ni journalisé en clair."""

    __tablename__ = "user"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=320, unique=True, index=True)
    hashed_password: str = Field(max_length=255)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
