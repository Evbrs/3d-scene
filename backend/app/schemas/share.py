"""Schémas du partage de vue (`docs/spec-complete.md` §3.5, phase P8)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ViewState(BaseModel):
    """Configuration d'affichage partagée.

    Volontairement fermée (`extra="forbid"`) et bornée : `state` est écrit par un client et relu
    par un endpoint **public**. Accepter un JSON libre en ferait un vecteur de stockage arbitraire
    servi sans authentification.
    """

    model_config = ConfigDict(extra="forbid")

    camera_preset: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9\-]+$")
    visible_faces: list[Annotated[str, Field(max_length=16)]] = Field(
        default_factory=list, max_length=128
    )
    transparent_faces: list[Annotated[str, Field(max_length=16)]] = Field(
        default_factory=list, max_length=128
    )
    camera_position: tuple[float, float, float] | None = None
    room_index: Annotated[int, Field(ge=0, le=999)] = 0


class SharedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ViewState
    label: str | None = Field(default=None, max_length=100)
    # Durée de vie optionnelle : un lien de partage éternel est un risque qui ne se referme
    # jamais. Absent = pas d'expiration (comportement par défaut de la spec).
    expires_in_days: Annotated[int, Field(ge=1, le=365)] | None = None


class SharedViewRead(BaseModel):
    """Vue destinée au **propriétaire** : contient le jeton, donc jamais exposée publiquement."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    token: str
    state: dict[str, Any]
    created_at: datetime


class PublicSceneResponse(BaseModel):
    """Réponse de l'endpoint public.

    Ne contient **aucune** information sur le propriétaire : ni identifiant, ni adresse e-mail,
    ni date de modification du projet. Un lien de partage ne doit rien révéler de plus que la
    vue elle-même.
    """

    kind: Literal["shared-view"] = "shared-view"
    project_name: str
    state: dict[str, Any]
    scene: dict[str, Any]
