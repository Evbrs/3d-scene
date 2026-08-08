"""Modèle du plan : Project → Room → Face → Element.

Arbitrages figés par `docs/spec-complete.md` §8 et repris tels quels ici :
- la géométrie (polygone de pièce, revêtements, couleurs) est stockée en colonnes JSON, pas
  normalisée — la migration vers un modèle normalisé n'aura lieu que si un vrai besoin de
  requête apparaît (§8, cas 1) ;
- l'édition concurrente utilise un verrouillage optimiste par champ `version` (§8, cas 3).
"""

from datetime import datetime
from typing import Any, ClassVar

# Pas de `from __future__ import annotations` dans ce module : SQLModel résout les annotations
# de `Relationship` à l'exécution, et une annotation devenue chaîne ("list['Room']") est refusée
# par SQLAlchemy (« seems to be using a generic class as the argument to relationship() »).
from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, UniqueConstraint, text
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlmodel import Field, Relationship

from app.models.base import ElementKind, FaceKind, TimestampedModel, json_type, value_enum

# `version_id_col` de SQLAlchemy attend l'objet `Column` lui-même. Sous SQLModel, l'attribut de
# classe n'est pas encore une `Column` au moment où `__mapper_args__` est lu : on construit donc
# la colonne en amont et on la référence des deux côtés.
_project_version_column = Column(
    "version", Integer, nullable=False, default=1, server_default=text("1")
)


def _cascade(order_by: str) -> dict[str, Any]:
    """Options communes aux relations enfant supprimées avec leur parent.

    `order_by` : sans lui, PostgreSQL rend les lignes dans l'ordre physique du heap. Un `UPDATE`
    y réécrit la ligne en fin de table, si bien qu'un simple renommage change l'ordre des pièces —
    et le frontend, qui sélectionne la dernière pièce après création, fait alors dessiner
    l'utilisateur dans la mauvaise. Invisible sur SQLite, dont le parcours suit le rowid.

    `passive_deletes` : la cascade est déjà posée en base (`ondelete="CASCADE"`). Sans cette
    option, SQLAlchemy charge tout l'arbre pour le supprimer ligne à ligne — des dizaines de
    SELECT pour un résultat que la base produit seule.
    """
    return {"cascade": "all, delete-orphan", "order_by": order_by, "passive_deletes": True}


# Bornes physiques, alignées sur celles que l'API applique déjà (`app/schemas/plan.py`). Les
# répéter en base n'est pas de la redondance : SQLAdmin, la CLI, Celery et `psql` écrivent sans
# passer par Pydantic, et SQLModel désactive la validation sur les modèles `table=True`.
MAX_CENTIMETERS = 10_000
MAX_FURNITURE_CENTIMETERS = 1_000
# Étendue du plan lui-même : `app/schemas/plan.py::Coordinate` accepte un sommet entre -100 000 et
# +100 000 cm, soit un kilomètre de part et d'autre de l'origine. Les positions du mobilier libre
# vivent dans ce même repère et sont donc bornées à l'identique.
MAX_PLAN_COORDINATE = 100_000


class Project(TimestampedModel, table=True):
    """Un projet de rénovation, racine de l'arbre du plan."""

    __tablename__ = "project"
    # Index composite calqué sur la requête réelle de `GET /api/projects` :
    # `WHERE organization_id IN (…) ORDER BY updated_at DESC`. Deux index séparés obligeraient
    # PostgreSQL à trier après filtrage ; celui-ci sert le filtre *et* l'ordre. Il porte
    # `organization_id` depuis que l'appartenance a remplacé la propriété : indexé sur `owner_id`,
    # il ne servait plus aucune requête.
    __table_args__ = (Index("ix_project_organization_updated", "organization_id", "updated_at"),)

    # Verrouillage optimiste (spec §8, cas 3) : SQLAlchemy incrémente `version` à chaque UPDATE
    # et lève `StaleDataError` si la ligne a changé entre-temps, au lieu d'écraser silencieusement.
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": _project_version_column}

    id: int | None = Field(default=None, primary_key=True)
    # Locataire propriétaire du projet : c'est **lui** qui porte les droits d'accès
    # (`app/api/permissions.py`). Pas d'index dédié : `ix_project_organization_updated` a
    # `organization_id` en tête, donc sert aussi le filtre seul.
    organization_id: int = Field(foreign_key="organization.id", ondelete="CASCADE")
    # Trace de création, et **rien d'autre**. Comparer `owner_id` à l'utilisateur courant pour
    # autoriser un accès a été retiré partout : un projet appartient à l'organisation, pas à la
    # personne qui a cliqué la première (`docs/strategie-produit.md` §6, point 1).
    owner_id: int = Field(foreign_key="user.id", ondelete="CASCADE")
    # Déclassement en lecture seule (`docs/strategie-produit.md` §4). Un chantier excédentaire au
    # regard du palier reçoit cette date : il reste **lisible, exportable et partageable**, il
    # n'est plus modifiable. Il n'est jamais supprimé — c'est la seule issue qui ne détruise pas la
    # confiance, et la plus favorable au réabonnement.
    archived_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    version: int = Field(default=1, sa_column=_project_version_column)

    rooms: list["Room"] = Relationship(
        back_populates="project", sa_relationship_kwargs=_cascade("Room.id")
    )
    shared_views: list["SharedView"] = Relationship(
        back_populates="project", sa_relationship_kwargs=_cascade("SharedView.id")
    )


class Room(TimestampedModel, table=True):
    """Une pièce d'un projet.

    `wall_thickness_cm` est le champ ajouté par la spec §3.1 : sans épaisseur, pas d'extrusion
    3D possible.
    """

    __tablename__ = "room"
    __table_args__ = (
        CheckConstraint(
            f"wall_thickness_cm > 0 AND wall_thickness_cm <= {MAX_CENTIMETERS}",
            name="ck_room_wall_thickness_cm_bounded",
        ),
        CheckConstraint(
            f"ceiling_height_cm > 0 AND ceiling_height_cm <= {MAX_CENTIMETERS}",
            name="ck_room_ceiling_height_cm_bounded",
        ),
        # `length(name) > 0` et non `trim` : c'est exactement ce que l'API refuse
        # (`min_length=1`). Une contrainte plus stricte que l'API transformerait une requête
        # aujourd'hui acceptée en erreur 500.
        CheckConstraint("length(name) > 0", name="ck_room_name_not_empty"),
        # --- Fond de plan (spec §10, amendement A5) ---
        # `NULL` veut dire « image posée, pas encore calibrée ». Une valeur par défaut inventée
        # serait indiscernable d'un calibrage réel, et l'artisan dessinerait un logement faux
        # sans en être averti.
        CheckConstraint(
            "background_scale_cm_per_px IS NULL OR (background_scale_cm_per_px > 0 "
            f"AND background_scale_cm_per_px <= {MAX_CENTIMETERS})",
            name="ck_room_background_scale_bounded",
        ),
        CheckConstraint(
            "background_opacity >= 0 AND background_opacity <= 1",
            name="ck_room_background_opacity_bounded",
        ),
        CheckConstraint(
            "background_rotation_deg >= -360 AND background_rotation_deg <= 360",
            name="ck_room_background_rotation_deg_bounded",
        ),
        CheckConstraint(
            f"background_offset_x_cm >= -{MAX_PLAN_COORDINATE} "
            f"AND background_offset_x_cm <= {MAX_PLAN_COORDINATE} "
            f"AND background_offset_y_cm >= -{MAX_PLAN_COORDINATE} "
            f"AND background_offset_y_cm <= {MAX_PLAN_COORDINATE}",
            name="ck_room_background_offsets_bounded",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=200)
    wall_thickness_cm: float = Field(default=10.0, gt=0)
    ceiling_height_cm: float = Field(default=250.0, gt=0)

    # Polygone libre de la pièce (spec §1 : « polygones libres »), liste de sommets [x, y] en cm,
    # ordonnés dans le sens trigonométrique. JSON assumé (§8, cas 1).
    polygon: list[list[float]] = Field(
        default_factory=list,
        sa_column=Column(
            MutableList.as_mutable(json_type()), nullable=False, server_default=text("'[]'")
        ),
    )

    # --- Fond de plan calibré (spec §10, amendement A5) ---------------------------------------
    # L'image elle-même vit ailleurs (stockage de fichiers) : la pièce n'en porte que le calage,
    # exprimé dans le repère du plan. Le téléversement et l'outil de calibrage à deux clics sont
    # des lots distincts ; ce qui est figé ici, c'est le contrat de données.
    background_url: str | None = Field(default=None, max_length=500)
    background_scale_cm_per_px: float | None = Field(default=None)
    background_offset_x_cm: float = Field(
        default=0.0, sa_column_kwargs={"server_default": text("0")}
    )
    background_offset_y_cm: float = Field(
        default=0.0, sa_column_kwargs={"server_default": text("0")}
    )
    background_rotation_deg: float = Field(
        default=0.0, sa_column_kwargs={"server_default": text("0")}
    )
    background_opacity: float = Field(
        default=1.0, sa_column_kwargs={"server_default": text("1")}
    )

    project: Project = Relationship(back_populates="rooms")
    faces: list["Face"] = Relationship(
        back_populates="room", sa_relationship_kwargs=_cascade("Face.id")
    )
    # Mobilier posé au sol, adossé à aucune face (spec §10, amendement A4). Nommée `free_elements`
    # et non `elements` : `room.faces[*].elements` existe déjà et désigne autre chose — confondre
    # les deux listes ferait compter deux fois le mobilier d'une pièce.
    free_elements: list["Element"] = Relationship(
        back_populates="room", sa_relationship_kwargs=_cascade("Element.id")
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
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )

    room: Room = Relationship(back_populates="faces")
    elements: list["Element"] = Relationship(
        back_populates="face", sa_relationship_kwargs=_cascade("Element.id")
    )


class FurnitureType(TimestampedModel, table=True):
    """Recette de composition d'un meuble générique (spec §4.1).

    Ce n'est pas un modèle 3D : c'est une liste de primitives en coordonnées relatives, mise à
    l'échelle par les dimensions de l'instance au moment du rendu.

    Défini avant `Element` : SQLAlchemy ne sait pas résoudre une annotation de relation écrite
    sous forme de chaîne contenant une union (`"FurnitureType | None"`).
    """

    __tablename__ = "furnituretype"
    __table_args__ = (
        CheckConstraint(
            f"default_width_cm > 0 AND default_width_cm <= {MAX_FURNITURE_CENTIMETERS}",
            name="ck_furnituretype_default_width_cm_bounded",
        ),
        CheckConstraint(
            f"default_height_cm > 0 AND default_height_cm <= {MAX_FURNITURE_CENTIMETERS}",
            name="ck_furnituretype_default_height_cm_bounded",
        ),
        CheckConstraint(
            f"default_depth_cm > 0 AND default_depth_cm <= {MAX_FURNITURE_CENTIMETERS}",
            name="ck_furnituretype_default_depth_cm_bounded",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=100, unique=True, index=True)
    name: str = Field(max_length=200)
    category: str = Field(max_length=50, index=True)

    color_slots: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            MutableList.as_mutable(json_type()), nullable=False, server_default=text("'[]'")
        ),
    )
    parts: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(
            MutableList.as_mutable(json_type()), nullable=False, server_default=text("'[]'")
        ),
    )
    # Paramètres de variation acceptés par la recette (spec §4.4 : « défini dans la recette du
    # `FurnitureType`, pas dans le moteur de rendu générique »). Chaque entrée déclare
    # `{"name", "axis", "applies_to", "min", "max"}` ; l'instance ne fait que choisir une valeur
    # dans son `variant_params`. Une recette sans `variants` n'a aucun paramètre : le
    # `variant_params` de ses instances est ignoré, faute de savoir ce qu'il piloterait.
    variants: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(
            MutableList.as_mutable(json_type()), nullable=False, server_default=text("'[]'")
        ),
    )

    # Dimensions par défaut proposées à l'instanciation (spec §4.4).
    default_width_cm: float = Field(default=100.0, gt=0)
    default_height_cm: float = Field(default=100.0, gt=0)
    default_depth_cm: float = Field(default=50.0, gt=0)

    elements: list["Element"] = Relationship(back_populates="furniture_type")


class Element(TimestampedModel, table=True):
    """Un élément du plan : ouverture ou meuble, ancré à une face **ou** à une pièce.

    Deux ancrages, exactement un par ligne (spec §10, amendement A4) :

    - `face_id` — pose sur une face. `x_offset_cm` / `y_offset_cm` sont les coordonnées 2D déjà
      utilisées par l'éditeur, projetées en profondeur selon l'épaisseur du mur lors du calcul du
      scene graph (spec §3.1). C'est le cas **obligatoire** des ouvertures et le cas naturel de ce
      qui est accroché au mur ;
    - `room_id` — pose au sol de la pièce. `pos_x_cm` / `pos_y_cm` donnent le **centre** de
      l'emprise dans le repère du plan, celui de `Room.polygon`. Sans lui, un lit, une table ou un
      îlot étaient littéralement impossibles : le modèle obligeait à les coller contre un mur.

    Le centre et non un coin : la rotation autour de la verticale est libre, et tourner autour
    d'un coin déplacerait le meuble au lieu de l'orienter.
    """

    __tablename__ = "element"
    __table_args__ = (
        CheckConstraint(
            f"width_cm > 0 AND width_cm <= {MAX_CENTIMETERS}", name="ck_element_width_cm_bounded"
        ),
        CheckConstraint(
            f"height_cm > 0 AND height_cm <= {MAX_CENTIMETERS}", name="ck_element_height_cm_bounded"
        ),
        CheckConstraint(
            f"depth_cm > 0 AND depth_cm <= {MAX_CENTIMETERS}", name="ck_element_depth_cm_bounded"
        ),
        CheckConstraint(
            "rotation_deg >= -360 AND rotation_deg <= 360", name="ck_element_rotation_deg_bounded"
        ),
        # Un décalage négatif place l'élément hors de sa face : `element_fits_on_face` le refuse
        # déjà, mais seulement quand la face a une géométrie connue.
        CheckConstraint(
            "x_offset_cm >= 0 AND y_offset_cm >= 0", name="ck_element_offsets_not_negative"
        ),
        # --- Ancrage (spec §10, amendement A4) ---
        # Exactement un des deux repères, et jamais les deux à la fois. En base et pas seulement
        # dans Pydantic : SQLAdmin, la CLI, Celery et `psql` écrivent sans passer par l'API, et
        # les `Field(...)` de SQLModel sont **inertes** sur un modèle `table=True` (leçon de la
        # vague 1). Les coordonnées sont incluses dans la contrainte : une ligne qui porterait à
        # la fois un décalage de face et une position de pièce n'aurait pas de repère décidable.
        CheckConstraint(
            "(face_id IS NOT NULL AND room_id IS NULL "
            "AND pos_x_cm IS NULL AND pos_y_cm IS NULL) "
            "OR (face_id IS NULL AND room_id IS NOT NULL "
            "AND pos_x_cm IS NOT NULL AND pos_y_cm IS NOT NULL)",
            name="ck_element_exactly_one_anchor",
        ),
        # Une ouverture est un percement du mur (spec §3.1) : un trou qui flotte au milieu d'une
        # pièce ne veut rien dire. `kind` est stocké sous sa valeur (`value_enum`), d'où le
        # littéral en minuscules.
        CheckConstraint(
            "kind = 'furniture' OR face_id IS NOT NULL",
            name="ck_element_opening_needs_a_face",
        ),
        CheckConstraint(
            f"(pos_x_cm IS NULL OR (pos_x_cm >= -{MAX_PLAN_COORDINATE} "
            f"AND pos_x_cm <= {MAX_PLAN_COORDINATE})) "
            f"AND (pos_y_cm IS NULL OR (pos_y_cm >= -{MAX_PLAN_COORDINATE} "
            f"AND pos_y_cm <= {MAX_PLAN_COORDINATE}))",
            name="ck_element_position_bounded",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    face_id: int | None = Field(
        default=None, foreign_key="face.id", index=True, ondelete="CASCADE"
    )
    # Ancrage au sol de la pièce, exclusif de `face_id`. Indexé : c'est par lui que le calcul du
    # scene graph et la suppression en cascade retrouvent le mobilier libre d'une pièce.
    room_id: int | None = Field(
        default=None, foreign_key="room.id", index=True, ondelete="CASCADE"
    )
    kind: ElementKind = Field(  # type: ignore[call-overload]
        default=ElementKind.FURNITURE, sa_type=value_enum(ElementKind, "elementkind")
    )

    # Renseignés pour un élément ancré à une face, et pour lui seul. Ils gardent leur valeur par
    # défaut sur un meuble libre, où ils ne sont lus par personne : le discriminant est `face_id`.
    x_offset_cm: float = Field(default=0.0)
    y_offset_cm: float = Field(default=0.0)
    # Centre de l'emprise au sol, dans le repère du plan — renseignés pour un meuble libre, et
    # pour lui seul.
    pos_x_cm: float | None = Field(default=None)
    pos_y_cm: float | None = Field(default=None)
    width_cm: float = Field(default=100.0, gt=0)
    height_cm: float = Field(default=100.0, gt=0)
    depth_cm: float = Field(default=50.0, gt=0)
    rotation_deg: float = Field(default=0.0)

    # Renseignés uniquement pour `kind == FURNITURE` (spec §5).
    furniture_type_id: int | None = Field(
        default=None, foreign_key="furnituretype.id", index=True, ondelete="SET NULL"
    )
    colors: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )
    variant_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )

    face: Face | None = Relationship(back_populates="elements")
    room: Room | None = Relationship(back_populates="free_elements")
    furniture_type: FurnitureType | None = Relationship(back_populates="elements")

    @property
    def anchor_room(self) -> Room:
        """La pièce qui porte l'élément, quel que soit son ancrage.

        Point de passage unique depuis l'amendement A4 : `element.face.room` était partout, et
        lève désormais un `AttributeError` — donc une 500 — sur le premier meuble libre venu.

        Les deux relations doivent avoir été chargées d'avance par l'appelant : sous session
        asynchrone, un chargement paresseux ici lèverait `MissingGreenlet`. La contrainte
        `ck_element_exactly_one_anchor` garantit qu'exactement une des deux est renseignée, d'où
        le refus explicite du cas impossible plutôt qu'un `None` propagé plus loin.
        """
        room = self.room if self.face is None else self.face.room
        if room is None:
            raise ValueError(f"élément {self.id} sans ancrage : ni face ni pièce")
        return room


class SharedView(TimestampedModel, table=True):
    """Lien permalien de partage d'une vue 3D (spec §3.5).

    Exposé par un endpoint public en lecture seule (P8) : `state` ne doit contenir que de la
    configuration d'affichage, jamais d'information sensible.

    Le cycle de vie du lien — expiration, révocation, libellé — vit dans de vraies colonnes et non
    dans `state`. Une expiration rangée dans un blob JSON n'est ni indexable, ni contrôlable par
    la base, ni visible depuis SQLAdmin : rien n'empêchait de rouvrir un partage fermé en éditant
    le JSON à la main.
    """

    __tablename__ = "sharedview"

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    token: str = Field(max_length=64, unique=True, index=True)

    # {"visible_faces": [...], "transparent_faces": [...], "camera_preset": "...",
    #  "camera_position": [x, y, z]} — voir spec §3.4 et §3.5.
    state: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )

    # Indexée : la purge périodique des liens morts balaie la table sur ce seul critère.
    expires_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True, index=True
    )
    # Révocation sans suppression : garder la ligne conserve la trace du partage et empêche la
    # réattribution du jeton, là où un DELETE efface aussi la preuve qu'il a existé.
    revoked_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    # `label` est le nom donné par le propriétaire pour s'y retrouver dans sa liste de liens ;
    # `public_label` est le seul des deux qu'un visiteur non authentifié pourra voir.
    label: str | None = Field(default=None, max_length=100)
    public_label: str | None = Field(default=None, max_length=100)

    view_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    last_viewed_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    # Lien protégé par mot de passe. Colonne posée dès maintenant : `sharedview` est lue par
    # l'endpoint public à chaque affichage, et on ne veut la migrer qu'une fois.
    password_hash: str | None = Field(default=None, max_length=255)

    project: Project = Relationship(back_populates="shared_views")
