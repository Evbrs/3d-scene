"""Modèle du plan : Project → Room → Face → Element.

Arbitrages figés par `docs/spec-complete.md` §8 et repris tels quels ici :
- la géométrie (polygone de pièce, revêtements, couleurs) est stockée en colonnes JSON, pas
  normalisée — la migration vers un modèle normalisé n'aura lieu que si un vrai besoin de
  requête apparaît (§8, cas 1) ;
- l'édition concurrente utilise un verrouillage optimiste par champ `version` (§8, cas 3).
"""

from typing import Any, ClassVar

# Pas de `from __future__ import annotations` dans ce module : SQLModel résout les annotations
# de `Relationship` à l'exécution, et une annotation devenue chaîne ("list['Room']") est refusée
# par SQLAlchemy (« seems to be using a generic class as the argument to relationship() »).
from sqlalchemy import Column, Integer, UniqueConstraint
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship

from app.models.base import ElementKind, FaceKind, TimestampedModel, value_enum

# `version_id_col` de SQLAlchemy attend l'objet `Column` lui-même. Sous SQLModel, l'attribut de
# classe n'est pas encore une `Column` au moment où `__mapper_args__` est lu : on construit donc
# la colonne en amont et on la référence des deux côtés.
_project_version_column = Column("version", Integer, nullable=False, default=1)


class Project(TimestampedModel, table=True):
    """Un projet de rénovation, racine de l'arbre du plan."""

    __tablename__ = "project"

    # Verrouillage optimiste (spec §8, cas 3) : SQLAlchemy incrémente `version` à chaque UPDATE
    # et lève `StaleDataError` si la ligne a changé entre-temps, au lieu d'écraser silencieusement.
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": _project_version_column}

    id: int | None = Field(default=None, primary_key=True)
    # Propriétaire du projet : socle des permissions objet (spec §7, P2).
    owner_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=200, index=True)
    description: str | None = Field(default=None, max_length=2000)
    version: int = Field(default=1, sa_column=_project_version_column)

    rooms: list["Room"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    shared_views: list["SharedView"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Room(TimestampedModel, table=True):
    """Une pièce d'un projet.

    `wall_thickness_cm` est le champ ajouté par la spec §3.1 : sans épaisseur, pas d'extrusion
    3D possible.
    """

    __tablename__ = "room"

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=200)
    wall_thickness_cm: float = Field(default=10.0, gt=0)
    ceiling_height_cm: float = Field(default=250.0, gt=0)

    # Polygone libre de la pièce (spec §1 : « polygones libres »), liste de sommets [x, y] en cm,
    # ordonnés dans le sens trigonométrique. JSON assumé (§8, cas 1).
    polygon: list[list[float]] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON), nullable=False)
    )

    project: Project = Relationship(back_populates="rooms")
    faces: list["Face"] = Relationship(
        back_populates="room",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Face(TimestampedModel, table=True):
    """Une face d'une pièce : mur, sol ou plafond.

    Les murs sont lettrés automatiquement (A, B, C…) dans l'ordre du polygone — l'attribution
    est faite par l'API (P3), la colonne `label` ne fait que la stocker.
    """

    __tablename__ = "face"
    __table_args__ = (UniqueConstraint("room_id", "label", name="uq_face_room_label"),)

    id: int | None = Field(default=None, primary_key=True)
    room_id: int = Field(foreign_key="room.id", index=True, ondelete="CASCADE")
    label: str = Field(max_length=8)
    kind: FaceKind = Field(  # type: ignore[call-overload]
        default=FaceKind.WALL, sa_type=value_enum(FaceKind, "facekind")
    )

    # Segment du mur dans le plan 2D, en cm. Nul pour le sol et le plafond, dont la géométrie
    # se déduit du polygone de la pièce.
    start_x_cm: float | None = Field(default=None)
    start_y_cm: float | None = Field(default=None)
    end_x_cm: float | None = Field(default=None)
    end_y_cm: float | None = Field(default=None)

    # Revêtement : {"color": "#RRGGBB", "material": "...", "unit_width_cm": n,
    #               "unit_height_cm": n, "pattern": "chevron"} — voir spec §1.
    covering: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableDict.as_mutable(JSON), nullable=False)
    )

    room: Room = Relationship(back_populates="faces")
    elements: list["Element"] = Relationship(
        back_populates="face",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class FurnitureType(TimestampedModel, table=True):
    """Recette de composition d'un meuble générique (spec §4.1).

    Ce n'est pas un modèle 3D : c'est une liste de primitives en coordonnées relatives, mise à
    l'échelle par les dimensions de l'instance au moment du rendu.

    Défini avant `Element` : SQLAlchemy ne sait pas résoudre une annotation de relation écrite
    sous forme de chaîne contenant une union (`"FurnitureType | None"`).
    """

    __tablename__ = "furnituretype"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=100, unique=True, index=True)
    name: str = Field(max_length=200)
    category: str = Field(max_length=50, index=True)

    color_slots: list[str] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON), nullable=False)
    )
    parts: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON), nullable=False)
    )

    # Dimensions par défaut proposées à l'instanciation (spec §4.4).
    default_width_cm: float = Field(default=100.0, gt=0)
    default_height_cm: float = Field(default=100.0, gt=0)
    default_depth_cm: float = Field(default=50.0, gt=0)

    elements: list["Element"] = Relationship(back_populates="furniture_type")


class Element(TimestampedModel, table=True):
    """Un élément posé sur une face : ouverture ou meuble.

    `x_offset_cm` / `y_offset_cm` sont les coordonnées 2D déjà utilisées par l'éditeur, projetées
    en profondeur selon l'épaisseur du mur lors du calcul du scene graph (spec §3.1).
    """

    __tablename__ = "element"

    id: int | None = Field(default=None, primary_key=True)
    face_id: int = Field(foreign_key="face.id", index=True, ondelete="CASCADE")
    kind: ElementKind = Field(  # type: ignore[call-overload]
        default=ElementKind.FURNITURE, sa_type=value_enum(ElementKind, "elementkind")
    )

    x_offset_cm: float = Field(default=0.0)
    y_offset_cm: float = Field(default=0.0)
    width_cm: float = Field(default=100.0, gt=0)
    height_cm: float = Field(default=100.0, gt=0)
    depth_cm: float = Field(default=50.0, gt=0)
    rotation_deg: float = Field(default=0.0)

    # Renseignés uniquement pour `kind == FURNITURE` (spec §5).
    furniture_type_id: int | None = Field(
        default=None, foreign_key="furnituretype.id", index=True, ondelete="SET NULL"
    )
    colors: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(MutableDict.as_mutable(JSON), nullable=False)
    )
    variant_params: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableDict.as_mutable(JSON), nullable=False)
    )

    face: Face = Relationship(back_populates="elements")
    furniture_type: FurnitureType | None = Relationship(back_populates="elements")


class SharedView(TimestampedModel, table=True):
    """Lien permalien de partage d'une vue 3D (spec §3.5).

    Exposé par un endpoint public en lecture seule (P8) : `state` ne doit contenir que de la
    configuration d'affichage, jamais d'information sensible.
    """

    __tablename__ = "sharedview"

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    token: str = Field(max_length=64, unique=True, index=True)

    # {"visible_faces": [...], "transparent_faces": [...], "camera_preset": "...",
    #  "camera_position": [x, y, z]} — voir spec §3.4 et §3.5.
    state: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(MutableDict.as_mutable(JSON), nullable=False)
    )

    project: Project = Relationship(back_populates="shared_views")
