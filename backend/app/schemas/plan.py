"""Schémas Pydantic imbriqués de l'API du plan 2D (`docs/spec-complete.md` §7, phase P3).

Séparés des modèles SQLModel volontairement : ce que l'API accepte n'est pas ce que la base
stocke. Un client ne doit jamais pouvoir fixer un `id`, un `owner_id` ou un horodatage.
"""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import ElementKind, FaceKind, LayingPattern
from app.services.faces import (
    polygon_area,
    polygon_crosses_itself,
    shortest_side_length,
)

# Bornes physiques : une pièce de 10 km ou un mur de 2 mm ne relèvent pas de la rénovation. Ces
# validations sont côté serveur, jamais seulement côté client (conventions OWASP du projet).
Centimeters = Annotated[float, Field(gt=0, le=10_000)]
Coordinate = Annotated[float, Field(ge=-100_000, le=100_000)]

# Un mur plus court qu'un centimètre n'est pas un mur : c'est un sommet en double que
# l'utilisateur n'a pas vu passer. Il produit pourtant une face, une lettre, et une direction
# calculée par normalisation d'un vecteur quasi nul.
MIN_WALL_LENGTH_CM = 1.0
# Aire minimale d'une pièce, en cm² : 10 cm sur 10 cm. En dessous, le contour est dégénéré (tous
# les sommets alignés, ou repliés) et son aire signée ne détermine plus d'orientation fiable.
MIN_ROOM_AREA_CM2 = 100.0

# Un seul type de couleur pour toute l'API. Avoir deux validations distinctes — un motif
# hexadécimal ici, un simple « commence par # et fait 7 caractères » là — laissait passer
# `#zzzzzz` sur un emplacement de meuble alors que la même valeur était refusée sur un revêtement.
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]

# Les blobs JSON libres sont bornés en nombre d'entrées **et** en taille de chaque entrée. Borner
# le seul nombre de clés laissait passer 61 Mo par élément : `{"a": "<30 Mo>"}` fait une entrée.
BlobKey = Annotated[str, Field(max_length=50)]
# Un paramètre de variante décrit une répétition ou un choix nommé (spec §4.4), pas un document :
# `bool` avant `int` pour que `true` ne soit pas relu comme `1` par une union permissive.
VariantValue = (
    bool
    | Annotated[int, Field(ge=-1_000_000, le=1_000_000)]
    | Annotated[float, Field(ge=-1e9, le=1e9)]
    | Annotated[str, Field(max_length=100)]
    | None
)
ColorSlots = Annotated[dict[BlobKey, HexColor], Field(max_length=24)]
VariantParams = Annotated[dict[BlobKey, VariantValue], Field(max_length=32)]


class PartialUpdate(BaseModel):
    """Base des schémas de modification partielle.

    Un champ **absent** veut dire « ne touche pas » — c'est ce que `exclude_unset` exprime. Un
    champ à `null` voudrait dire « écris NULL », ce que refusent toutes les colonnes du plan sauf
    celles énumérées par `NULLABLE_FIELDS`. Sans ce contrôle, `{"width_cm": null}` traversait la
    validation, partait en violation `NOT NULL` et ressortait en 500 : n'importe quel client
    authentifié pouvait provoquer une erreur serveur sur n'importe quelle route de modification.

    La liste est déclarée par sous-classe et non déduite de l'annotation : celle-ci porte déjà un
    `| None` pour dire « facultatif », les deux sens y sont confondus.
    """

    model_config = ConfigDict(extra="forbid")

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"version"})

    @model_validator(mode="after")
    def _reject_meaningless_nulls(self) -> "PartialUpdate":
        offenders = sorted(
            field
            for field in self.model_fields_set
            if field not in self.NULLABLE_FIELDS and getattr(self, field, None) is None
        )
        if offenders:
            raise ValueError(
                f"champ(s) envoyé(s) à null sans signification : {', '.join(offenders)} — "
                "omettez-les pour ne pas les modifier"
            )
        return self


class Covering(BaseModel):
    """Revêtement d'une face (`docs/spec-complete.md` §1)."""

    model_config = ConfigDict(extra="forbid")

    color: HexColor | None = None
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
    colors: ColorSlots = Field(default_factory=dict)
    variant_params: VariantParams = Field(default_factory=dict)


class ElementCreate(ElementBase):
    version: int | None = Field(default=None, ge=1)


class ElementUpdate(PartialUpdate):
    # `furniture_type_id: null` détache l'élément du catalogue, `colors`/`variant_params: null`
    # les vident — comme `covering: null` sur une face. Les trois sont donc des nuls porteurs de
    # sens, contrairement aux dimensions.
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"version", "furniture_type_id", "colors", "variant_params"}
    )

    kind: ElementKind | None = None
    x_offset_cm: Coordinate | None = None
    y_offset_cm: Coordinate | None = None
    width_cm: Centimeters | None = None
    height_cm: Centimeters | None = None
    depth_cm: Centimeters | None = None
    rotation_deg: Annotated[float, Field(ge=-360, le=360)] | None = None
    furniture_type_id: int | None = None
    # Mêmes types que sur `ElementBase`. Redéclarer `dict[str, str]` ici contournait la validation
    # des couleurs : la valeur invalide était écrite en base, puis faisait échouer la
    # sérialisation de *toutes* les lectures traversant cet élément (500 permanent).
    colors: ColorSlots | None = None
    variant_params: VariantParams | None = None
    version: int | None = Field(default=None, ge=1)


class ElementRead(ElementBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    face_id: int


# --- Face -------------------------------------------------------------------------------------


class FaceUpdate(PartialUpdate):
    """Une face n'est pas créée ni supprimée directement : elle découle du polygone de la pièce.

    Seul son revêtement est modifiable par le client.
    """

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"version", "covering"})

    covering: Covering | None = None
    # Verrouillage optimiste : version du projet lue par le client (voir `ProjectUpdate`).
    version: int | None = Field(default=None, ge=1)


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
        """Un polygone est soit vide (pièce esquissée), soit un vrai contour fermé et simple.

        Deux sommets identiques consécutifs produiraient un mur de longueur nulle, donc une face
        dégénérée que le calcul du scene graph (P6) ne saurait pas orienter. Un contour qui se
        croise est pire : son aire signée ne détermine plus d'orientation, et toutes les normales
        sortantes de la pièce s'inversent d'un appel à l'autre.
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
        shortest = shortest_side_length(value)
        if shortest < MIN_WALL_LENGTH_CM:
            raise ValueError(
                f"mur de {round(shortest, 3)} cm : un mur doit mesurer au moins "
                f"{MIN_WALL_LENGTH_CM} cm"
            )
        area = polygon_area(value)
        if area < MIN_ROOM_AREA_CM2:
            raise ValueError(
                f"contour de {round(area, 2)} cm² : une pièce doit couvrir au moins "
                f"{MIN_ROOM_AREA_CM2} cm²"
            )
        if polygon_crosses_itself(value):
            raise ValueError("le contour se croise lui-même")
        return value


class RoomCreate(RoomBase):
    version: int | None = Field(default=None, ge=1)


class RoomUpdate(PartialUpdate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    wall_thickness_cm: Centimeters | None = None
    ceiling_height_cm: Centimeters | None = None
    polygon: Polygon | None = None
    # Verrouillage optimiste : version du projet lue par le client (voir `ProjectUpdate`).
    version: int | None = Field(default=None, ge=1)
    # Confirme explicitement la suppression de murs portant des éléments (perte de données).
    force: bool = False

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


class ProjectUpdate(PartialUpdate):
    # `description: null` efface la description : la colonne, elle, est bien nullable.
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"version", "description"})

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


ConflictCode = Literal["stale_version", "destructive_change"]


class ConflictDetail(BaseModel):
    """Corps de réponse d'un 409 sur conflit d'édition.

    Déclaré dans les `responses` des routes d'écriture : le schéma OpenAPI est la source de
    vérité du frontend (`docs/plan-generation-ia.md` §6), un 409 non documenté y serait
    invisible. `current_version` est aussi renvoyé dans l'en-tête `X-Current-Version`.

    `code` est le champ sur lequel le client aiguille, et c'est la raison d'être de ce modèle :
    un test de sous-chaîne sur `detail` — ce que faisait le frontend — casse à la première
    reformulation d'un message. Deux natures de conflit seulement :

    - `stale_version` : quelqu'un a écrit entre-temps, il faut recharger puis rejouer ;
    - `destructive_change` : la requête est cohérente mais détruirait du travail déjà posé, et
      n'aboutira qu'avec une confirmation explicite (`force: true`).

    `current_version` est nul dans le seul cas où la collision est détectée par la base sans
    qu'on sache encore de quel projet il s'agit (filet de sécurité, voir `app/api/conflicts.py`).
    """

    detail: str
    current_version: int | None = None
    code: ConflictCode = "stale_version"
