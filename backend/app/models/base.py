"""Briques communes aux modèles.

Les énumérations sont stockées en base sous leur *valeur* (chaîne), pas sous leur nom Python :
c'est ce qui rend le JSON de l'API et le contenu de la base lisibles sans table de conversion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def json_type() -> JSON:
    """Type JSON portable : `JSONB` sur PostgreSQL, `JSON` textuel partout ailleurs.

    `JSONB` est stocké décomposé : il est indexable et ne repasse pas par un analyseur syntaxique
    à chaque lecture. `with_variant` le réserve à PostgreSQL et laisse SQLite — moteur par défaut
    de la suite de tests (`tests/conftest.py`) — sur le `JSON` qu'il sait traiter, sans quoi la
    portabilité affichée par la conftest serait fausse.
    """
    return JSON().with_variant(JSONB(), "postgresql")


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

    Les deux colonnes portent en plus un `server_default` : sans lui, un `INSERT` écrit à la main
    dans `psql` — la voie la plus courte en incident — échoue sur une violation `NOT NULL` de
    colonnes que l'auteur de la requête n'a aucune raison de connaître.

    `onupdate` ne se substitue pas à une affectation explicite : SQLAlchemy ne l'applique qu'aux
    colonnes absentes du `SET` de l'`UPDATE`. Une écriture qui veut « toucher » la ligne sans
    rien changer d'autre doit donc toujours affecter `updated_at` elle-même.
    """

    # `type: ignore` : la surcharge de `Field` déclare `sa_type: type[Any]`, alors que SQLModel
    # accepte parfaitement une *instance* de type à l'exécution — c'est le seul moyen de passer
    # `timezone=True`.
    created_at: datetime = Field(  # type: ignore[call-overload]
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
        nullable=False,
    )
    updated_at: datetime = Field(  # type: ignore[call-overload]
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": utcnow},
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
    """Motif de pose d'un revêtement (`docs/spec-complete.md` §1).

    Aucune colonne n'est typée sur cette énumération : le motif vit dans le blob `Face.covering`,
    colonne JSON assumée par §8 (cas 1). Y ajouter une valeur ne demande donc **aucune migration**
    (spec §10, amendement A8) — mais change le schéma OpenAPI publié.
    """

    STRAIGHT = "straight"
    STAGGERED = "staggered"
    # Ajoutée par l'amendement A8 : `WASTE_RATIO_BY_PATTERN` provisionnait sa chute de 12 % depuis
    # la vague 2, et aucune saisie ne pouvait produire le motif.
    DIAGONAL = "diagonal"
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
