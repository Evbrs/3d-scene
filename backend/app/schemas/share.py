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
    # Même durcissement que `camera_preset` : `label` est écrit par un client, stocké dans
    # `state`, et restitué par un endpoint public sans authentification. Sans motif, ce sont
    # 100 octets de contenu arbitraire servis à des tiers.
    # Liste **noire** plutôt que blanche : un libellé français normal contient accents, tirets
    # cadratins et apostrophes typographiques, qu'une liste blanche finirait toujours par
    # refuser à tort. Ce qui est interdit, ce sont les caractères de balisage et de contrôle —
    # les seuls qui posent problème dans une valeur restituée par un endpoint public.
    label: str | None = Field(default=None, max_length=100, pattern=r"^[^<>&\"`\x00-\x1f]*$")
    # Titre montré au visiteur, distinct du nom du projet. Le nom d'un projet de rénovation est
    # rarement neutre — « Rénovation Dupont, 12 rue des Lilas » — et il partait jusqu'ici tel quel
    # dans une réponse servie sans authentification. Même durcissement que `label`, pour la même
    # raison : la valeur est écrite par un client et restituée à des tiers.
    public_label: str | None = Field(
        default=None, max_length=100, pattern=r"^[^<>&\"`\x00-\x1f]*$"
    )
    # Durée de vie optionnelle : un lien de partage éternel est un risque qui ne se referme
    # jamais. Absent = pas d'expiration (comportement par défaut de la spec).
    expires_in_days: Annotated[int, Field(ge=1, le=365)] | None = None


class SharedViewRead(BaseModel):
    """Vue destinée au **propriétaire** : contient le jeton, donc jamais exposée publiquement.

    Le cycle de vie du lien y figure explicitement : sans `expires_at` ni `revoked_at`, le
    propriétaire n'a aucun moyen de distinguer, dans sa liste, un lien vivant d'un lien fermé.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    token: str
    state: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    label: str | None = None
    public_label: str | None = None


class PublicSceneResponse(BaseModel):
    """Réponse de l'endpoint public.

    Ne contient **aucune** information sur le propriétaire : ni identifiant, ni adresse e-mail,
    ni date de modification du projet. Un lien de partage ne doit rien révéler de plus que la
    vue elle-même.

    `project_name` est le titre à afficher, et **jamais** le nom brut du projet : il vaut
    `public_label` quand le propriétaire en a posé un, et un libellé neutre sinon. La spec §3.5
    exige « pas d'info sensible exposée » d'un lien public, or le nom d'un projet de rénovation
    porte couramment le nom et l'adresse du client — et un lien de partage se transfère par SMS.
    Un défaut ouvert obligerait chaque propriétaire à penser à se protéger. Le champ garde ce nom
    parce que c'est celui que le viewer public lit déjà.
    """

    kind: Literal["shared-view"] = "shared-view"
    project_name: str
    public_label: str | None = None
    state: dict[str, Any]
    scene: dict[str, Any]
