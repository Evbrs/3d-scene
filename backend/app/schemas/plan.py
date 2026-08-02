"""Schémas Pydantic imbriqués de l'API du plan 2D (`docs/spec-complete.md` §7, phase P3).

Séparés des modèles SQLModel volontairement : ce que l'API accepte n'est pas ce que la base
stocke. Un client ne doit jamais pouvoir fixer un `id`, un `owner_id` ou un horodatage.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.base import ElementKind, FaceKind, LayingPattern

# Bornes physiques : une pièce de 10 km ou un mur de 2 mm ne relèvent pas de la rénovation. Ces
# validations sont côté serveur, jamais seulement côté client (conventions OWASP du projet).
Centimeters = Annotated[float, Field(gt=0, le=10_000)]
Coordinate = Annotated[float, Field(ge=-100_000, le=100_000)]


class Covering(BaseModel):
    """Revêtement d'une face (`docs/spec-complete.md` §1)."""

    model_config = ConfigDict(extra="forbid")

    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    material: str | None = Field(default=None, max_length=100)
    unit_width_cm: Centimeters | None = None
    unit_height_cm: Centimeters | None = None
    pattern: LayingPattern | None = None


# --- Element ----------------------------------------------------------------------------------


class ElementBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ElementKind = ElementKind.FURNITURE
    x_offset_cm: Coordinate = 0.0
    y_offset_cm: Coordinate = 0.0
    width_cm: Centimeters = 100.0
    height_cm: Centimeters = 100.0
    depth_cm: Centimeters = 50.0
    rotation_deg: Annotated[float, Field(ge=-360, le=360)] = 0.0
    furniture_type_id: int | None = None
    colors: dict[str, str] = Field(default_factory=dict)
    variant_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("colors")
    @classmethod
    def _validate_colors(cls, value: dict[str, str]) -> dict[str, str]:
        for slot, color in value.items():
            if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
                raise ValueError(f"couleur invalide pour l'emplacement {slot!r} : {color!r}")
        return value


class ElementCreate(ElementBase):
    pass


class ElementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ElementKind | None = None
    x_offset_cm: Coordinate | None = None
    y_offset_cm: Coordinate | None = None
    width_cm: Centimeters | None = None
    height_cm: Centimeters | None = None
    depth_cm: Centimeters | None = None
    rotation_deg: Annotated[float, Field(ge=-360, le=360)] | None = None
    furniture_type_id: int | None = None
    colors: dict[str, str] | None = None
    variant_params: dict[str, Any] | None = None


class ElementRead(ElementBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    face_id: int


# --- Face -------------------------------------------------------------------------------------


class FaceUpdate(BaseModel):
    """Une face n'est pas créée ni supprimée directement : elle découle du polygone de la pièce.

    Seuls son revêtement et sa hauteur sont modifiables par le client.
    """

    model_config = ConfigDict(extra="forbid")

    covering: Covering | None = None


class FaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    label: str
    kind: FaceKind
    start_x_cm: float | None
    start_y_cm: float | None
    end_x_cm: float | None
    end_y_cm: float | None
    covering: dict[str, Any]
    elements: list[ElementRead] = Field(default_factory=list)


# --- Room -------------------------------------------------------------------------------------

Polygon = Annotated[list[Annotated[list[Coordinate], Field(min_length=2, max_length=2)]], Field()]


class RoomBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    wall_thickness_cm: Centimeters = 10.0
    ceiling_height_cm: Centimeters = 250.0
    polygon: Polygon = Field(default_factory=list)

    @field_validator("polygon")
    @classmethod
    def _validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        """Un polygone est soit vide (pièce esquissée), soit un vrai contour fermé.

        Deux sommets identiques consécutifs produiraient un mur de longueur nulle, donc une face
        dégénérée que le calcul du scene graph (P6) ne saurait pas orienter.
        """
        if not value:
            return value
        if len(value) < 3:
            raise ValueError("un polygone doit avoir au moins 3 sommets")
        if len(value) > 64:
            raise ValueError("un polygone est limité à 64 sommets")
        for index, vertex in enumerate(value):
            following = value[(index + 1) % len(value)]
            if vertex == following:
                raise ValueError(f"sommets identiques consécutifs à l'index {index}")
        return value


class RoomCreate(RoomBase):
    pass


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    wall_thickness_cm: Centimeters | None = None
    ceiling_height_cm: Centimeters | None = None
    polygon: Polygon | None = None

    _validate_polygon = field_validator("polygon")(RoomBase._validate_polygon.__func__)  # type: ignore[attr-defined]


class RoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    wall_thickness_cm: float
    ceiling_height_cm: float
    polygon: list[list[float]]
    faces: list[FaceRead] = Field(default_factory=list)


# --- Project ----------------------------------------------------------------------------------


class ProjectBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # Verrouillage optimiste (spec §8, cas 3) : le client renvoie la version qu'il a lue. Une
    # version différente en base signifie que quelqu'un d'autre a écrit entre-temps.
    version: int | None = Field(default=None, ge=1)


class ProjectSummary(BaseModel):
    """Vue légère, pour la liste des projets : pas de chargement de tout l'arbre."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectRead(ProjectSummary):
    """Vue complète : le plan entier, tel que consommé par l'éditeur 2D et le viewer 3D."""

    rooms: list[RoomRead] = Field(default_factory=list)


class Page(BaseModel):
    """Enveloppe de pagination — une liste nue empêcherait d'ajouter un total plus tard."""

    total: int
    limit: int
    offset: int


class ProjectPage(Page):
    items: list[ProjectSummary]


class ConflictDetail(BaseModel):
    """Corps de réponse d'un 409 sur conflit d'édition."""

    detail: Literal["Le projet a été modifié entre-temps"]
    current_version: int
