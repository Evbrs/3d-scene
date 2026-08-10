"""Schémas Pydantic imbriqués de l'API du plan 2D (`docs/spec-complete.md` §7, phase P3).

Séparés des modèles SQLModel volontairement : ce que l'API accepte n'est pas ce que la base
stocke. Un client ne doit jamais pouvoir fixer un `id`, un `owner_id` ou un horodatage.
"""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import ElementKind, FaceKind, LayingPattern
from app.services.faces import (
    OPENING_KINDS,
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

# Un lot borné (spec §10, amendement A6) : sans borne, une seule requête tient une transaction
# ouverte aussi longtemps qu'elle veut, et le verrou de version du projet avec elle.
MAX_BATCH_OPERATIONS = 100


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
    # Taux de chute imposé pour cette face, en points de base (800 = 8 %). Absent, c'est la
    # provision du motif qui s'applique (`app/geometry/quantities.py`), et c'est le cas courant.
    #
    # Il vit ici et non sur `price_item` (spec §10, amendement A14) : la chute est une propriété
    # **physique** de la pose — coupes de rive, casse, rattrapage de trame — au même titre que le
    # motif et les dimensions d'unité, qui sont déjà ici. Ce n'est pas du chiffrage, et c'est ce
    # qui la distingue de `face_costing`. Le poseur qui sait que le grand format lui coûte 15 % le
    # dit sur la face qu'il vient de décrire, sans quitter l'écran.
    #
    # Borné à 100 % : au-delà on commanderait plus du double de la surface posée, ce qui est une
    # faute de frappe et non un choix de métier.
    waste_ratio_bp: Annotated[int, Field(ge=0, le=10_000)] | None = None


# --- Element ----------------------------------------------------------------------------------


class ElementShape(BaseModel):
    """Ce qu'un élément est, indépendamment de l'endroit où il est posé.

    Séparé de son placement depuis l'amendement A4 : un élément s'ancre à une face **ou** au sol
    d'une pièce, et les deux repères n'ont ni le même nom de champ ni la même signification.
    Faire hériter les deux ancrages d'une base commune évite de dupliquer les bornes.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ElementKind = ElementKind.FURNITURE
    width_cm: Centimeters = 100.0
    height_cm: Centimeters = 100.0
    depth_cm: Centimeters = 50.0
    rotation_deg: Annotated[float, Field(ge=-360, le=360)] = 0.0
    furniture_type_id: int | None = None
    colors: ColorSlots = Field(default_factory=dict)
    variant_params: VariantParams = Field(default_factory=dict)


class ElementBase(ElementShape):
    """Élément adossé à une face : les décalages sont mesurés dans le plan de cette face."""

    x_offset_cm: Coordinate = 0.0
    y_offset_cm: Coordinate = 0.0


class ElementCreate(ElementBase):
    version: int | None = Field(default=None, ge=1)


class RoomElementBody(ElementShape):
    """Meuble posé au sol, ancré à la pièce et non à une face (spec §10, amendement A4).

    `pos_x_cm` / `pos_y_cm` sont le **centre** de l'emprise, dans le repère du plan — celui de
    `Room.polygon`, celui que l'éditeur 2D manipule déjà. Ils sont obligatoires : un meuble libre
    sans position n'a pas d'endroit où être dessiné, et une valeur par défaut le poserait
    silencieusement à l'origine du plan, souvent hors de la pièce.
    """

    pos_x_cm: Coordinate
    pos_y_cm: Coordinate

    @model_validator(mode="after")
    def _refuse_an_opening_without_a_wall(self) -> "RoomElementBody":
        """Une ouverture est un percement du mur (spec §3.1) : elle ne flotte pas dans la pièce.

        La contrainte existe aussi en base (`ck_element_opening_needs_a_face`) ; la doubler ici
        n'est pas de la redondance mais la différence entre un 422 explicite et une 500.
        """
        if self.kind in OPENING_KINDS:
            raise ValueError(
                f"une ouverture ({self.kind.value}) doit être posée sur un mur : "
                "utilisez POST /api/faces/{face_id}/elements"
            )
        return self


class RoomElementCreate(RoomElementBody):
    version: int | None = Field(default=None, ge=1)


class ElementPatch(PartialUpdate):
    """Modification d'un élément, **sans** verrouillage : la version est portée par le lot.

    Le changement d'ancrage est volontairement absent (spec §10, A4) : `face_id` et `room_id`
    n'y figurent pas. Passer d'un repère à l'autre change le sens des coordonnées, donc exige un
    placement complet — c'est une suppression suivie d'une création, pas une modification.
    """

    # `furniture_type_id: null` détache l'élément du catalogue, `colors`/`variant_params: null`
    # les vident — comme `covering: null` sur une face. Les trois sont donc des nuls porteurs de
    # sens, contrairement aux dimensions.
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"furniture_type_id", "colors", "variant_params"}
    )

    kind: ElementKind | None = None
    x_offset_cm: Coordinate | None = None
    y_offset_cm: Coordinate | None = None
    pos_x_cm: Coordinate | None = None
    pos_y_cm: Coordinate | None = None
    width_cm: Centimeters | None = None
    height_cm: Centimeters | None = None
    depth_cm: Centimeters | None = None
    rotation_deg: Annotated[float, Field(ge=-360, le=360)] | None = None
    furniture_type_id: int | None = None
    # Mêmes types que sur `ElementShape`. Redéclarer `dict[str, str]` ici contournait la
    # validation des couleurs : la valeur invalide était écrite en base, puis faisait échouer la
    # sérialisation de *toutes* les lectures traversant cet élément (500 permanent).
    colors: ColorSlots | None = None
    variant_params: VariantParams | None = None


class ElementUpdate(ElementPatch):
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = ElementPatch.NULLABLE_FIELDS | {"version"}

    version: int | None = Field(default=None, ge=1)


class ElementRead(ElementBase):
    """Vue d'un élément, les deux ancrages exposés tels quels.

    Le discriminant est `face_id` : quand il est nul, l'élément est posé au sol de `room_id` et
    ce sont `pos_x_cm` / `pos_y_cm` qui font foi. `x_offset_cm` / `y_offset_cm` gardent alors
    leur valeur par défaut et ne veulent rien dire — ne pas les lire.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    face_id: int | None
    room_id: int | None
    pos_x_cm: float | None
    pos_y_cm: float | None


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

# Échelle du fond de plan. Bornée haut comme bas : à 1e-6 cm/px une image de 2 000 px couvrirait
# deux millimètres, et le calibrage à deux clics rendrait un plan invisible sans dire pourquoi.
BackgroundScale = Annotated[float, Field(gt=0, le=10_000)]
Opacity = Annotated[float, Field(ge=0, le=1)]


class RoomBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    wall_thickness_cm: Centimeters = 10.0
    ceiling_height_cm: Centimeters = 250.0
    polygon: Polygon = Field(default_factory=list)

    # --- Fond de plan (spec §10, amendement A5) ---
    # L'échelle reste nulle tant que le calibrage n'a pas eu lieu : « image posée, pas encore
    # calibrée » est un état réel, et une valeur inventée serait indiscernable d'une mesure.
    background_url: Annotated[str, Field(max_length=500)] | None = None
    background_scale_cm_per_px: BackgroundScale | None = None
    background_offset_x_cm: Coordinate = 0.0
    background_offset_y_cm: Coordinate = 0.0
    background_rotation_deg: Annotated[float, Field(ge=-360, le=360)] = 0.0
    background_opacity: Opacity = 1.0

    @field_validator("background_url")
    @classmethod
    def _validate_background_url(cls, value: str | None) -> str | None:
        """Refuse tout ce qui n'est pas un chemin du site ou une URL `https://` (spec §10, A5).

        Ce champ est écrit par un client et relu dans un attribut d'image côté navigateur : c'est
        une entrée utilisateur au sens OWASP A03, et l'écriture est le seul endroit où sa
        validation ne dépend pas de la vigilance du rendu. Trois formes sont explicitement
        écartées :

        - `javascript:` et les autres schémas actifs, qui s'exécutent selon l'attribut d'accueil ;
        - `data:`, qui ferait porter des mégaoctets d'image à une colonne de 500 caractères ;
        - `//hôte/chemin`, protocol-relative, qui ressemble à un chemin local et désigne un tiers.
          `/\\hôte` est écarté au même titre : plusieurs navigateurs le lisent comme `//`.
        """
        if value is None:
            return None
        if any(character.isspace() or ord(character) < 0x20 for character in value):
            raise ValueError("l'adresse du fond de plan ne peut contenir ni espace ni contrôle")
        if value.startswith("/") and not value.startswith(("//", "/\\")):
            return value
        if value.startswith("https://") and len(value) > len("https://"):
            return value
        raise ValueError(
            "adresse de fond de plan refusée : attendu un chemin du site commençant par « / » "
            "ou une URL « https:// »"
        )

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


class RoomPatch(PartialUpdate):
    """Modification d'une pièce, **sans** verrouillage : la version est portée par le lot."""

    # `background_url: null` retire le fond de plan, `background_scale_cm_per_px: null` annule le
    # calibrage sans retirer l'image : les deux colonnes sont nullables et ces nuls ont un sens.
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"background_url", "background_scale_cm_per_px"}
    )

    name: str | None = Field(default=None, min_length=1, max_length=200)
    wall_thickness_cm: Centimeters | None = None
    ceiling_height_cm: Centimeters | None = None
    polygon: Polygon | None = None
    background_url: Annotated[str, Field(max_length=500)] | None = None
    background_scale_cm_per_px: BackgroundScale | None = None
    background_offset_x_cm: Coordinate | None = None
    background_offset_y_cm: Coordinate | None = None
    background_rotation_deg: Annotated[float, Field(ge=-360, le=360)] | None = None
    background_opacity: Opacity | None = None
    # Confirme explicitement la suppression de murs portant des éléments (perte de données).
    force: bool = False

    _validate_polygon = field_validator("polygon")(RoomBase._validate_polygon.__func__)  # type: ignore[attr-defined]
    _validate_background_url = field_validator("background_url")(
        RoomBase._validate_background_url.__func__  # type: ignore[attr-defined]
    )


class RoomUpdate(RoomPatch):
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = RoomPatch.NULLABLE_FIELDS | {"version"}

    # Verrouillage optimiste : version du projet lue par le client (voir `ProjectUpdate`).
    version: int | None = Field(default=None, ge=1)


class RoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    wall_thickness_cm: float
    ceiling_height_cm: float
    polygon: list[list[float]]
    background_url: str | None = None
    background_scale_cm_per_px: float | None = None
    background_offset_x_cm: float = 0.0
    background_offset_y_cm: float = 0.0
    background_rotation_deg: float = 0.0
    background_opacity: float = 1.0
    faces: list[FaceRead] = Field(default_factory=list)
    # Mobilier posé au sol, adossé à aucune face (spec §10, A4). Une liste à part et non fondue
    # dans les faces : le client doit pouvoir distinguer ce qui est accroché de ce qui est posé.
    free_elements: list[ElementRead] = Field(default_factory=list)


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


# --- Écriture en lot (spec §10, amendement A6) --------------------------------------------------


class BatchOperationBase(BaseModel):
    """Base des opérations d'un lot.

    Aucune ne porte de `version` : le lot en a **une**, en tête de requête, et c'est tout l'objet
    de l'amendement A6. Une version par opération réintroduirait la sérialisation qu'on supprime.
    """

    model_config = ConfigDict(extra="forbid")


class CreateFaceElementOp(BatchOperationBase):
    op: Literal["create_face_element"]
    face_id: int = Field(ge=1)
    element: ElementBase


class CreateRoomElementOp(BatchOperationBase):
    op: Literal["create_room_element"]
    room_id: int = Field(ge=1)
    element: RoomElementBody


class UpdateElementOp(BatchOperationBase):
    op: Literal["update_element"]
    element_id: int = Field(ge=1)
    changes: ElementPatch


class DeleteElementOp(BatchOperationBase):
    op: Literal["delete_element"]
    element_id: int = Field(ge=1)


class CreateRoomOp(BatchOperationBase):
    op: Literal["create_room"]
    room: RoomBase


class UpdateRoomOp(BatchOperationBase):
    op: Literal["update_room"]
    room_id: int = Field(ge=1)
    changes: RoomPatch


class DeleteRoomOp(BatchOperationBase):
    op: Literal["delete_room"]
    room_id: int = Field(ge=1)


# Union discriminée sur `op` : sans discriminant, Pydantic essaie les variantes une par une et
# rend l'erreur de la dernière, ce qui rendrait le message d'un lot refusé illisible.
BatchOperation = Annotated[
    CreateFaceElementOp
    | CreateRoomElementOp
    | UpdateElementOp
    | DeleteElementOp
    | CreateRoomOp
    | UpdateRoomOp
    | DeleteRoomOp,
    Field(discriminator="op"),
]


class BatchRequest(BaseModel):
    """Un lot d'écritures du plan, appliqué dans une seule transaction.

    Aucune opération ne peut désigner ce qu'une autre vient de créer : les identifiants d'un lot
    sont ceux que le client détient déjà. Un chaînage exigerait des identifiants temporaires, donc
    un protocole que rien ne réclame aujourd'hui.
    """

    model_config = ConfigDict(extra="forbid")

    # Verrouillage optimiste, une fois pour tout le lot (spec §8, cas 3).
    version: int | None = Field(default=None, ge=1)
    operations: list[BatchOperation] = Field(min_length=1, max_length=MAX_BATCH_OPERATIONS)


class BatchOperationResult(BaseModel):
    """Résultat d'une opération, rendu au **même rang** que l'opération d'origine.

    Les identifiants sont répétés hors de l'objet rendu : après une suppression il n'y a plus
    d'objet, et c'est pourtant là que le client a le plus besoin de savoir ce qui a disparu.
    """

    op: str
    status: Literal["created", "updated", "deleted"]
    element_id: int | None = None
    room_id: int | None = None
    element: ElementRead | None = None
    room: RoomRead | None = None


class BatchResponse(BaseModel):
    version: int
    results: list[BatchOperationResult]


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
