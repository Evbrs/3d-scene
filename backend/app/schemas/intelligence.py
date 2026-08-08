"""Schémas du moteur d'intelligence du plan.

Contrairement au métré, dont l'enveloppe reste volontairement libre, l'inspection est **entièrement
typée**. La raison est qu'elle est consommée par un panneau qui rend chaque anomalie cliquable :
`severity`, `focus` et `element_ids` pilotent de l'interface, et un champ renommé en silence casse
un comportement au lieu d'afficher un chiffre faux. Les typer, c'est le faire échouer en CI.

L'aménagement automatique suit la même règle : la proposition sert à créer des éléments réels
(`POST /api/rooms/{id}/elements`), donc chacun de ses champs doit être vérifiable.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.rules import Severity


class AnomalyRead(BaseModel):
    """Une anomalie de conformité.

    `focus` est un point du **plan** en centimètres, dans le repère de `Room.polygon` : c'est ce
    qui permet au panneau de recentrer l'éditeur 2D sans conversion. `null` quand l'anomalie ne
    désigne aucun endroit précis — une pièce sans ouverture n'a pas de coordonnée.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    severity: Severity
    message: str
    room_id: int | None = None
    room_name: str | None = None
    face_labels: list[str] = Field(default_factory=list)
    element_ids: list[int] = Field(default_factory=list)
    focus: list[float] | None = None
    measured_cm: float | None = None
    threshold_cm: float | None = None


class RoomInspectionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: int | None = None
    name: str | None = None
    counts: dict[str, int]


class InspectionRead(BaseModel):
    """Le rapport complet.

    `thresholds` republie les seuils appliqués : un rapport qui annonce « passage insuffisant »
    sans dire par rapport à quoi n'est pas vérifiable, et le mode accessible change la réponse.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int | None = None
    thresholds: dict[str, Any]
    rooms: list[RoomInspectionRead]
    anomalies: list[AnomalyRead]
    counts: dict[str, int]
    warnings: list[str]


class LayingPlanRead(BaseModel):
    """Enveloppe du calepinage optimisé.

    Le détail par face reste un dictionnaire libre, pour la même raison que le métré : sa forme
    dépend du revêtement, une face peinte n'a pas de calepinage du tout, et un `Union` de variantes
    alourdirait le schéma sans rien apporter au client. Ce qui est typé, c'est ce qui se lit :
    `cuts_saved` est le chiffre que l'artisan compare.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int | None = None
    rooms: list[dict[str, Any]]
    cuts_saved: int


class LayoutItemRead(BaseModel):
    """Un meuble proposé, prêt à être créé tel quel.

    `pos_x_cm` / `pos_y_cm` sont le **centre** de l'emprise dans le repère du plan, la convention
    du mobilier libre (`docs/spec-complete.md` §10, amendement A4). Le client n'a donc aucune
    conversion à faire pour transformer une proposition en éléments réels.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    width_cm: float
    depth_cm: float
    height_cm: float
    pos_x_cm: float
    pos_y_cm: float
    rotation_deg: float
    against_face_label: str | None = None
    clearance_cm: float


class LayoutProposalRead(BaseModel):
    """Une implantation valide, avec le détail de sa note.

    `breakdown` n'est pas de la décoration : sans lui, le classement est une boîte noire et
    l'utilisateur n'a aucun moyen de comprendre pourquoi la deuxième proposition perd.
    """

    model_config = ConfigDict(extra="forbid")

    rank: int
    score: float
    breakdown: dict[str, float]
    items: list[LayoutItemRead]


class LayoutRequest(BaseModel):
    """Ce qu'un client peut demander. Rien d'autre : `extra="forbid"`.

    Aucun seuil n'entre par le corps de la requête. Les rendre pilotables par le client
    transformerait un contrôle métier en paramètre d'affichage, et il suffirait de demander un
    seuil de 10 cm pour qu'un plan invivable devienne conforme.
    """

    model_config = ConfigDict(extra="forbid")

    program: Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")] | None = None
    count: Annotated[int, Field(ge=1, le=5)] = 3
    accessible: bool = False


class LayoutProposalsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: int | None = None
    program: str
    weights: dict[str, float]
    proposals: list[LayoutProposalRead]
    warnings: list[str]
