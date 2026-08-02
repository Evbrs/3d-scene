"""Briques communes aux modèles.

Les énumérations sont stockées en base sous leur *valeur* (chaîne), pas sous leur nom Python :
c'est ce qui rend le JSON de l'API et le contenu de la base lisibles sans table de conversion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def value_enum(enum_class: type[StrEnum], name: str) -> SAEnum:
    """Type SQL pour une énumération, stockée sous sa *valeur* et non sous son nom Python.

    Par défaut, SQLAlchemy persiste le *nom* du membre (`CEILING`) alors que l'API sérialise sa
    valeur (`"ceiling"`) : la base et le JSON divergent, et tout accès SQL direct doit connaître
    les deux conventions. `values_callable` aligne les deux.
    """
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda enum: [member.value for member in enum],
    )


def utcnow() -> datetime:
    """Horodatage UTC *aware*.

    `datetime.utcnow()` est déprécié depuis Python 3.12 et renvoie un datetime naïf, ce qui
    provoque des comparaisons incohérentes une fois relu depuis PostgreSQL.
    """
    return datetime.now(UTC)


class TimestampedModel(SQLModel):
    """Colonnes de traçabilité partagées par toutes les tables métier.

    `timezone=True` est indispensable : `utcnow()` produit des datetimes *aware*, et une colonne
    `TIMESTAMP WITHOUT TIME ZONE` les relirait naïfs — toute comparaison ultérieure lèverait
    « can't compare offset-naive and offset-aware datetimes ». `sa_type` (et non `sa_column`)
    parce qu'un objet `Column` ne peut pas être partagé entre plusieurs tables.
    """

    # `type: ignore` : la surcharge de `Field` déclare `sa_type: type[Any]`, alors que SQLModel
    # accepte parfaitement une *instance* de type à l'exécution — c'est le seul moyen de passer
    # `timezone=True`.
    created_at: datetime = Field(  # type: ignore[call-overload]
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    updated_at: datetime = Field(  # type: ignore[call-overload]
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )


class FaceKind(StrEnum):
    """Nature d'une face d'une pièce.

    Le plafond est une face à part entière (`docs/spec-complete.md` §1).
    """

    WALL = "wall"
    FLOOR = "floor"
    CEILING = "ceiling"


class ElementKind(StrEnum):
    """Nature d'un élément posé sur une face.

    Les ouvertures (`DOOR_*`, `WINDOW`) deviennent des trous dans le mur extrudé
    (`docs/spec-complete.md` §3.1) ; `FURNITURE` référence un `FurnitureType` (§4).
    """

    DOOR_HINGED = "door_hinged"
    DOOR_SLIDING = "door_sliding"
    WINDOW = "window"
    FURNITURE = "furniture"


class LayingPattern(StrEnum):
    """Motif de pose d'un revêtement (`docs/spec-complete.md` §1)."""

    STRAIGHT = "straight"
    STAGGERED = "staggered"
    CHEVRON = "chevron"
    HERRINGBONE = "herringbone"


class FurnitureCategory(StrEnum):
    """Catégories du catalogue cible (`docs/spec-complete.md` §4.3)."""

    GENERAL = "general"
    BATHROOM = "bathroom"
    BEDROOM = "bedroom"
    LIVING_ROOM = "living_room"
    KITCHEN = "kitchen"


class PartPrimitive(StrEnum):
    """Primitives disponibles dans une recette de composition (`docs/spec-complete.md` §4.1)."""

    BOX = "box"
    CYLINDER = "cylinder"
    SPHERE = "sphere"
