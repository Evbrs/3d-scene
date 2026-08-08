"""API CRUD du plan 2D (`docs/spec-complete.md` §7, phase P3).

Toutes les routes sont authentifiées et passent par les permissions objet
(`app/api/permissions.py`) : aucun identifiant fourni par le client n'est utilisé sans revérifier
l'appartenance à l'organisation qui porte le projet, et le rôle qu'y a l'appelant.
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import col, select

from app.api.conflicts import STALE_MESSAGE, ConflictAwareRoute, PlanConflict
from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import (
    accessible_organization_ids,
    default_organization_id,
    get_owned_element,
    get_owned_face,
    get_owned_project,
    get_owned_room,
)
from app.core.cache import scene_cache
from app.models.base import ElementKind, utcnow
from app.models.organization import OrganizationRole
from app.models.plan import Element, Face, FurnitureType, Project, Room
from app.schemas.plan import (
    BatchOperation,
    BatchOperationResult,
    BatchRequest,
    BatchResponse,
    ConflictDetail,
    CreateFaceElementOp,
    CreateRoomElementOp,
    CreateRoomOp,
    DeleteElementOp,
    DeleteRoomOp,
    ElementCreate,
    ElementPatch,
    ElementRead,
    ElementUpdate,
    FaceRead,
    FaceUpdate,
    ProjectCreate,
    ProjectPage,
    ProjectRead,
    ProjectSummary,
    ProjectUpdate,
    RoomCreate,
    RoomElementCreate,
    RoomRead,
    RoomUpdate,
    UpdateElementOp,
    UpdateRoomOp,
)
from app.services.faces import (
    OPENING_KINDS,
    FaceRemovalWouldLoseElements,
    element_fits_in_room,
    element_fits_on_face,
    openings_overlap,
    sync_room_faces,
)

router = APIRouter(prefix="/api", tags=["plan"], route_class=ConflictAwareRoute)

# Le 409 est documenté dans l'OpenAPI : c'est la source de vérité du frontend
# (`docs/plan-generation-ia.md` §6), un conflit non déclaré y serait invisible.
CONFLICT_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ConflictDetail,
        "description": "Le plan a été modifié entre-temps (verrouillage optimiste, spec §8).",
    }
}

# Champs d'un élément qui n'ont de sens que pour un meuble (spec §5 : « renseignés uniquement
# pour `kind == FURNITURE` »).
FURNITURE_ONLY_FIELDS = ("furniture_type_id", "colors", "variant_params")

# Champs d'une pièce dont la modification invalide la géométrie des faces et de ce qui y est posé.
RESYNCHRONIZING_FIELDS = frozenset({"polygon", "ceiling_height_cm", "wall_thickness_cm"})

# Champs de placement propres à chaque ancrage (spec §10, amendement A4). Les écrire sur le
# mauvais ancrage violerait `ck_element_exactly_one_anchor` : sans ce refus applicatif, la base
# rendrait une 500 là où le client attend un 422 qui explique.
FACE_PLACEMENT_FIELDS = frozenset({"x_offset_cm", "y_offset_cm"})
ROOM_PLACEMENT_FIELDS = frozenset({"pos_x_cm", "pos_y_cm"})


async def _claim_project(
    session: SessionDep, project: Project, client_version: int | None
) -> None:
    """Point de passage obligé de **toute** écriture sur le plan.

    Deux rôles indissociables (spec §8, cas 3 — « édition concurrente ») :

    1. refuser l'écriture si le client travaillait sur une version périmée du plan ;
    2. marquer le projet comme modifié, ce qui incrémente `version` via `version_id_col` et
       remonte `updated_at`.

    Sans le point 2, une modification de pièce, de face ou d'élément laisserait `version`
    inchangée : deux clients éditant le même plan resteraient en « dernière écriture gagne »,
    exactement l'option que la spec écarte. C'est aussi ce qui fait remonter un projet
    activement édité en tête de la liste triée par `updated_at`. L'affectation reste explicite
    malgré le `onupdate` du modèle : `onupdate` ne s'applique qu'à un `UPDATE` déjà émis, or
    toucher un projet par ailleurs inchangé est précisément le but ici.
    """
    if client_version is not None and client_version != project.version:
        raise PlanConflict(STALE_MESSAGE, current_version=project.version)
    project.updated_at = utcnow()


# Chargement en une passe de l'arbre complet : sans ça, sérialiser un projet déclenche une
# requête par pièce, par face et par élément (spec §8, cas 4 — N+1). `free_elements` en fait
# partie depuis l'amendement A4 : le mobilier libre n'est sous aucune face.
FULL_TREE = selectinload(Project.rooms).options(  # type: ignore[arg-type]
    selectinload(Room.faces).options(selectinload(Face.elements)),  # type: ignore[arg-type]
    selectinload(Room.free_elements),  # type: ignore[arg-type]
)

# Même chargement, à l'échelle d'une pièce.
FULL_ROOM = (
    selectinload(Room.faces).options(selectinload(Face.elements)),  # type: ignore[arg-type]
    selectinload(Room.free_elements),  # type: ignore[arg-type]
)

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable"
)


async def _load_full_project(session: SessionDep, project_id: int) -> Project:
    project = (
        await session.execute(
            select(Project).where(col(Project.id) == project_id).options(FULL_TREE)
        )
    ).scalar_one_or_none()
    # `scalar_one` levait ici une 500 dès que la ligne disparaissait entre la vérification de
    # propriété et la relecture — cas courant avec deux onglets ouverts sur le même plan.
    if project is None:
        raise _NOT_FOUND
    return project


async def _delete_row(session: SessionDep, model: type[Room] | type[Element], row_id: int) -> None:
    """Suppression ensembliste d'une ligne et, par la base, de tout ce qui en dépend.

    Les relations enfant sont en `passive_deletes` et la cascade est déclarée en base : un seul
    `DELETE` suffit là où la suppression par l'ORM chargeait l'arbre entier ligne à ligne.
    """
    await session.execute(delete(model).where(col(model.id) == row_id))


# --- Projets ----------------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, current_user: CurrentUser
) -> Project:
    # `owner_id` n'est plus qu'une trace de création : c'est `organization_id` qui portera les
    # droits d'accès (`app/api/permissions.py`).
    project = Project(
        **payload.model_dump(),
        owner_id=current_user.id or 0,
        organization_id=await default_organization_id(session, current_user),
    )
    session.add(project)
    await session.commit()
    return await _load_full_project(session, project.id or 0)


@router.get("/projects", response_model=ProjectPage)
async def list_projects(
    session: SessionDep,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectPage:
    """Liste paginée des projets accessibles à l'utilisateur.

    Vue résumée : charger l'arbre complet de chaque projet pour une liste serait un gâchis
    proportionnel à la taille des plans.

    Le filtre porte sur les organisations du compte et non sur `owner_id` : c'est exactement la
    forme que sert `ix_project_organization_updated` (`WHERE organization_id IN (…) ORDER BY
    updated_at DESC`), et c'est ce qui rend visible à tout un cabinet le chantier ouvert par l'un
    de ses membres.
    """
    organization_ids = await accessible_organization_ids(session, current_user)
    if not organization_ids:
        return ProjectPage(total=0, limit=limit, offset=offset, items=[])

    owned = col(Project.organization_id).in_(organization_ids)
    total = (
        await session.execute(select(func.count()).select_from(Project).where(owned))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(Project)
                .where(owned)
                .order_by(col(Project.updated_at).desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return ProjectPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[ProjectSummary.model_validate(row) for row in rows],
    )


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def read_project(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> Project:
    await get_owned_project(session, project_id, current_user)
    return await _load_full_project(session, project_id)


@router.patch(
    "/projects/{project_id}", response_model=ProjectRead, responses=CONFLICT_RESPONSE
)
async def update_project(
    project_id: int, payload: ProjectUpdate, session: SessionDep, current_user: CurrentUser
) -> Project:
    """Modification d'un projet, avec verrouillage optimiste (spec §8, cas 3).

    Si le client fournit `version`, une divergence est signalée par un 409 plutôt que par un
    écrasement silencieux — c'est l'arbitrage tranché par la spec.
    """
    project = await get_owned_project(
        session, project_id, current_user, OrganizationRole.EDITOR
    )

    changes = payload.model_dump(exclude_unset=True)
    await _claim_project(session, project, changes.pop("version", None))

    for field, value in changes.items():
        setattr(project, field, value)

    await _commit_or_conflict(session, project)
    return await _load_full_project(session, project_id)


async def _commit_or_conflict(session: SessionDep, project: Project) -> None:
    """Valide la transaction, en traduisant la collision détectée par SQLAlchemy en 409.

    `StaleDataError` survient quand deux transactions *réellement* concurrentes écrivent la même
    ligne : la vérification applicative de `_claim_project` ne peut pas l'attraper, elle lit une
    version qui peut changer juste après.
    """
    current_version = project.version
    try:
        await session.commit()
    except StaleDataError as exc:
        await session.rollback()
        raise PlanConflict(STALE_MESSAGE, current_version=current_version) from exc


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=CONFLICT_RESPONSE,
)
async def delete_project(
    project_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    """Supprime un projet et tout son arbre.

    La `version` est optionnelle comme sur les autres écritures, mais c'est ici qu'elle compte le
    plus : sans elle, la route la plus destructrice de l'API était la seule à rester en « dernière
    écriture gagne » — l'option que la spec §8 (cas 3) écarte explicitement.

    Elle transite par la chaîne de requête et non par un corps : `DELETE` avec corps n'est pas
    relayé de façon fiable par les intermédiaires, et plusieurs clients HTTP le refusent.
    """
    project = await get_owned_project(
        session, project_id, current_user, OrganizationRole.ADMIN
    )
    await _claim_project(session, project, version)
    # `session.delete` et non un `DELETE` ensembliste : c'est le seul moyen de conserver la
    # clause `WHERE version = ?` posée par `version_id_col`, donc de refuser une suppression
    # concurrente. Les enfants ne sont pas chargés pour autant — les relations sont en
    # `passive_deletes`, la cascade est exécutée par la base (73 SELECT économisés pour 10 pièces).
    await session.delete(project)
    await _commit_or_conflict(session, project)
    # Seul cas où l'invalidation par version ne suffit pas : aucune version future ne viendra
    # rendre les clés inatteignables, il faut donc les retirer.
    await scene_cache.forget_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Pièces -----------------------------------------------------------------------------------


async def _load_full_room(session: SessionDep, room_id: int) -> Room:
    room = (
        await session.execute(select(Room).where(col(Room.id) == room_id).options(*FULL_ROOM))
    ).scalar_one_or_none()
    if room is None:
        raise _NOT_FOUND
    return room


@router.post(
    "/projects/{project_id}/rooms",
    response_model=RoomRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
)
async def create_room(
    project_id: int, payload: RoomCreate, session: SessionDep, current_user: CurrentUser
) -> Room:
    """Crée une pièce et génère ses faces (murs lettrés A, B, C… + sol + plafond)."""
    project = await get_owned_project(
        session, project_id, current_user, OrganizationRole.EDITOR
    )

    values = payload.model_dump()
    await _claim_project(session, project, values.pop("version", None))

    room = Room(**values, project_id=project_id)
    session.add(room)
    await session.flush()
    await sync_room_faces(session, room)
    await _commit_or_conflict(session, project)
    return await _load_full_room(session, room.id or 0)


@router.get("/rooms/{room_id}", response_model=RoomRead)
async def read_room(room_id: int, session: SessionDep, current_user: CurrentUser) -> Room:
    await get_owned_room(session, room_id, current_user)
    return await _load_full_room(session, room_id)


@router.patch("/rooms/{room_id}", response_model=RoomRead, responses=CONFLICT_RESPONSE)
async def update_room(
    room_id: int, payload: RoomUpdate, session: SessionDep, current_user: CurrentUser
) -> Room:
    """Modifie une pièce. Un changement de polygone resynchronise les faces.

    Si la nouvelle forme supprime des murs portant des éléments, la requête est refusée (409)
    tant que `force: true` n'est pas envoyé : perdre des meubles et des ouvertures en réponse à
    un `200 OK` est une perte de données invisible pour l'utilisateur.
    """
    room = await get_owned_room(session, room_id, current_user, OrganizationRole.EDITOR)
    current_version = room.project.version
    await _claim_project(session, room.project, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version", "force"})
    for field, value in changes.items():
        setattr(room, field, value)

    # La revalidation ne dépend pas que du polygone. Abaisser `ceiling_height_cm` fait sortir du
    # mur toute fenêtre haute déjà posée, et `wall_thickness_cm` change l'emprise des murs : ces
    # deux champs déclenchaient jusqu'ici zéro contrôle, et le trou finissait hors du contour
    # extrudé — invisible dans l'éditeur 2D, absurde en 3D.
    if RESYNCHRONIZING_FIELDS & changes.keys():
        try:
            await sync_room_faces(session, room, force=payload.force)
        except FaceRemovalWouldLoseElements as exc:
            await session.rollback()
            raise PlanConflict(
                f"{exc}. Renvoyez la requête avec `force: true` pour confirmer la suppression.",
                current_version=current_version,
                code="destructive_change",
            ) from exc

    await _commit_or_conflict(session, room.project)
    return await _load_full_room(session, room_id)


@router.delete(
    "/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT, responses=CONFLICT_RESPONSE
)
async def delete_room(
    room_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    room = await get_owned_room(session, room_id, current_user, OrganizationRole.EDITOR)
    project = room.project
    await _claim_project(session, project, version)
    await _delete_row(session, Room, room_id)
    await _commit_or_conflict(session, project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Faces ------------------------------------------------------------------------------------


@router.get("/rooms/{room_id}/faces", response_model=list[FaceRead])
async def list_faces(room_id: int, session: SessionDep, current_user: CurrentUser) -> list[Face]:
    await get_owned_room(session, room_id, current_user)
    return list(
        (
            await session.execute(
                select(Face)
                .where(col(Face.room_id) == room_id)
                .options(selectinload(Face.elements))  # type: ignore[arg-type]
                .order_by(col(Face.id))
            )
        )
        .scalars()
        .all()
    )


@router.patch("/faces/{face_id}", response_model=FaceRead, responses=CONFLICT_RESPONSE)
async def update_face(
    face_id: int, payload: FaceUpdate, session: SessionDep, current_user: CurrentUser
) -> Face:
    """Met à jour le revêtement d'une face.

    Ni création ni suppression : les faces découlent du polygone de la pièce.
    """
    face = await get_owned_face(session, face_id, current_user, OrganizationRole.EDITOR)
    await _claim_project(session, face.room.project, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "covering" in changes:
        # `covering: null` efface explicitement le revêtement ; ne pas envoyer le champ ne
        # change rien. Confondre les deux rendait l'effacement impossible.
        covering = changes["covering"] or {}
        face.covering = {key: value for key, value in covering.items() if value is not None}
    await _commit_or_conflict(session, face.room.project)

    reloaded = (
        await session.execute(
            select(Face)
            .where(col(Face.id) == face_id)
            .options(selectinload(Face.elements))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if reloaded is None:
        raise _NOT_FOUND
    return reloaded


# --- Éléments ---------------------------------------------------------------------------------


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _reject_invalid_placement(element: Element, face: Face | None, room: Room) -> None:
    """Refuse un élément mal posé, sur sa face comme au sol de sa pièce.

    Sans ce contrôle, une fenêtre posée à 350 cm sur un mur long de 180 cm est acceptée, et c'est
    le calcul du scene graph (P6) qui produit une géométrie absurde — très loin du point
    d'insertion, donc très coûteuse à diagnostiquer.

    Le recouvrement de deux ouvertures est vérifié au même endroit et pour la même raison : deux
    trous sécants ne sont pas triangulables, et le mur se retrouve percé de travers.

    `face is None` : l'élément est ancré à la pièce (spec §10, amendement A4). C'est alors son
    **emprise tournée** qui est confrontée au polygone, et non son seul centre — un centre dans la
    pièce ne dit rien d'un lit de 2 m dont la moitié traverse le mur.
    """
    if face is None:
        problem = element_fits_in_room(element, room)
        if problem is not None:
            raise _unprocessable(problem)
        return
    for problem in (
        element_fits_on_face(element, face, room),
        openings_overlap(element, face),
    ):
        if problem is not None:
            raise _unprocessable(problem)


def _reject_misplaced_anchor_fields(element: Element, changes: dict[str, Any]) -> None:
    """Refuse un placement écrit dans le repère de l'autre ancrage.

    `PATCH` ne change pas d'ancrage (spec §10, A4) : les deux repères n'ont ni la même origine ni
    la même signification, et passer de l'un à l'autre exige un placement complet. Sans ce refus,
    `{"pos_x_cm": 120}` sur une applique murale violerait `ck_element_exactly_one_anchor` et
    ressortirait en 500 au lieu d'un 422 qui explique.
    """
    forbidden = ROOM_PLACEMENT_FIELDS if element.face_id is not None else FACE_PLACEMENT_FIELDS
    carried = sorted(forbidden & changes.keys())
    if not carried:
        return
    anchor = "adossé à une face" if element.face_id is not None else "posé au sol de la pièce"
    raise _unprocessable(
        f"cet élément est {anchor} : {', '.join(carried)} appartient à l'autre repère. "
        "Changer d'ancrage se fait en supprimant puis en recréant l'élément."
    )


def _reject_incoherent_element(kind: ElementKind, values: dict[str, Any]) -> None:
    """Refuse une ouverture qui porterait des attributs de meuble.

    La vérification porte sur l'état **fusionné** et non sur la charge utile : un `PATCH` qui ne
    change que `kind` transformerait sinon un meuble en fenêtre en lui laissant sa recette et ses
    couleurs, et la contrainte serait contournée en deux requêtes au lieu d'une.
    """
    if kind not in OPENING_KINDS:
        return
    carried = [field for field in FURNITURE_ONLY_FIELDS if values.get(field)]
    if carried:
        raise _unprocessable(
            f"une ouverture ({kind.value}) ne porte pas de {', '.join(carried)} : "
            "ces champs ne valent que pour un meuble"
        )


async def _check_furniture_type(session: SessionDep, furniture_type_id: int | None) -> None:
    """Le catalogue est global : on valide l'existence, pas la propriété."""
    await _check_furniture_types(session, {furniture_type_id})


async def _check_furniture_types(session: SessionDep, wanted: set[int | None]) -> None:
    """Même contrôle, en une seule requête pour tout un lot (spec §10, amendement A6)."""
    identifiers = {value for value in wanted if value is not None}
    if not identifiers:
        return
    known = set(
        (
            await session.execute(
                select(FurnitureType.id).where(col(FurnitureType.id).in_(identifiers))
            )
        )
        .scalars()
        .all()
    )
    unknown = sorted(identifiers - known)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Type de mobilier inconnu : {', '.join(str(value) for value in unknown)}",
        )


@router.post(
    "/faces/{face_id}/elements",
    response_model=ElementRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
)
async def create_element(
    face_id: int, payload: ElementCreate, session: SessionDep, current_user: CurrentUser
) -> Element:
    face = await get_owned_face(session, face_id, current_user, OrganizationRole.EDITOR)
    values = payload.model_dump(exclude={"version"})
    # Toutes les validations qui interrogent la base passent **avant** `_claim_project` : après,
    # le projet est marqué modifié, et la moindre lecture déclenche un autoflush qui remonte la
    # collision de version sous forme de 500 au lieu du 409 attendu.
    _reject_incoherent_element(payload.kind, values)
    await _check_furniture_type(session, payload.furniture_type_id)
    await _claim_project(session, face.room.project, payload.version)

    element = Element(**values, face_id=face_id)
    _reject_invalid_placement(element, face, face.room)

    session.add(element)
    try:
        await _commit_or_conflict(session, face.room.project)
    except IntegrityError as exc:
        await session.rollback()
        raise _unprocessable("Élément invalide") from exc
    await session.refresh(element)
    return element


@router.post(
    "/rooms/{room_id}/elements",
    response_model=ElementRead,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT_RESPONSE,
)
async def create_room_element(
    room_id: int, payload: RoomElementCreate, session: SessionDep, current_user: CurrentUser
) -> Element:
    """Pose un meuble au sol de la pièce, adossé à aucune face (spec §10, amendement A4).

    C'est la route qui rend un lit, une table ou un îlot possibles. Elle est distincte de
    `POST /faces/{face_id}/elements` et non un mode de celle-ci : le placement n'est pas exprimé
    dans le même repère, et une route qui accepterait les deux ne saurait pas lequel valider.
    """
    room = await get_owned_room(session, room_id, current_user, OrganizationRole.EDITOR)
    values = payload.model_dump(exclude={"version"})
    # Comme sur la route de face : tout ce qui interroge la base passe **avant** `_claim_project`.
    await _check_furniture_type(session, payload.furniture_type_id)
    await _claim_project(session, room.project, payload.version)

    element = Element(**values, room_id=room_id)
    _reject_invalid_placement(element, None, room)

    session.add(element)
    try:
        await _commit_or_conflict(session, room.project)
    except IntegrityError as exc:
        await session.rollback()
        raise _unprocessable("Élément invalide") from exc
    await session.refresh(element)
    return element


def _element_changes(payload: ElementPatch) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    # `colors: null` et `variant_params: null` **effacent**, comme `covering: null` sur une face ;
    # écrire NULL dans ces colonnes `NOT NULL` sortait en 500.
    for blob in ("colors", "variant_params"):
        if changes.get(blob, {}) is None:
            changes[blob] = {}
    return changes


@router.patch(
    "/elements/{element_id}", response_model=ElementRead, responses=CONFLICT_RESPONSE
)
async def update_element(
    element_id: int, payload: ElementUpdate, session: SessionDep, current_user: CurrentUser
) -> Element:
    element = await get_owned_element(
        session, element_id, current_user, OrganizationRole.EDITOR
    )

    changes = _element_changes(payload)
    merged = {field: getattr(element, field) for field in FURNITURE_ONLY_FIELDS} | changes
    _reject_incoherent_element(changes.get("kind") or element.kind, merged)
    _reject_misplaced_anchor_fields(element, changes)
    if "furniture_type_id" in changes:
        await _check_furniture_type(session, changes["furniture_type_id"])

    room = element.anchor_room
    await _claim_project(session, room.project, payload.version)
    for field, value in changes.items():
        setattr(element, field, value)
    _reject_invalid_placement(element, element.face, room)

    await _commit_or_conflict(session, room.project)
    await session.refresh(element)
    return element


@router.delete(
    "/elements/{element_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=CONFLICT_RESPONSE,
)
async def delete_element(
    element_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    element = await get_owned_element(
        session, element_id, current_user, OrganizationRole.EDITOR
    )
    project = element.anchor_room.project
    await _claim_project(session, project, version)
    await _delete_row(session, Element, element_id)
    await _commit_or_conflict(session, project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Écriture en lot ----------------------------------------------------------------------------


async def _batch_faces(
    session: SessionDep, project_id: int, operations: list[BatchOperation]
) -> dict[int, Face]:
    """Faces désignées par le lot, **restreintes au projet de l'URL**.

    C'est le cœur de sécurité de la route : l'appartenance a été vérifiée une fois, sur le projet
    nommé dans le chemin, et elle ne dit rien des identifiants portés par le corps de la requête.
    Sans la jointure sur `project_id`, un éditeur légitime chez un client pourrait poser une porte
    dans le chantier d'un autre en devinant un identifiant de face. Un identifiant étranger est
    traité comme inexistant (404), jamais 403 : c'est la règle A1.
    """
    wanted = {op.face_id for op in operations if isinstance(op, CreateFaceElementOp)}
    if not wanted:
        return {}
    rows = (
        (
            await session.execute(
                select(Face)
                .join(Room, col(Room.id) == col(Face.room_id))
                .where(col(Face.id).in_(wanted), col(Room.project_id) == project_id)
                .options(
                    selectinload(Face.room).selectinload(Room.project),  # type: ignore[arg-type]
                    selectinload(Face.elements),  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    found = {row.id or 0: row for row in rows}
    if wanted - found.keys():
        raise _NOT_FOUND
    return found


async def _batch_rooms(
    session: SessionDep, project_id: int, operations: list[BatchOperation]
) -> dict[int, Room]:
    wanted = {
        op.room_id
        for op in operations
        if isinstance(op, CreateRoomElementOp | UpdateRoomOp | DeleteRoomOp)
    }
    if not wanted:
        return {}
    rows = (
        (
            await session.execute(
                select(Room)
                .where(col(Room.id).in_(wanted), col(Room.project_id) == project_id)
                .options(selectinload(Room.project))  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )
    found = {row.id or 0: row for row in rows}
    if wanted - found.keys():
        raise _NOT_FOUND
    return found


async def _batch_elements(
    session: SessionDep, project_id: int, operations: list[BatchOperation]
) -> dict[int, Element]:
    """Éléments désignés par le lot, les deux ancrages chargés d'avance.

    Le filtre par projet ne peut pas se faire en SQL sans deux jointures alternatives — un
    élément pend d'une face **ou** d'une pièce (A4). Il est donc fait en Python sur
    `anchor_room`, ce qui reste une seule requête et laisse la règle lisible.
    """
    wanted = {
        op.element_id
        for op in operations
        if isinstance(op, UpdateElementOp | DeleteElementOp)
    }
    if not wanted:
        return {}
    rows = (
        (
            await session.execute(
                select(Element)
                .where(col(Element.id).in_(wanted))
                .options(
                    selectinload(Element.face)  # type: ignore[arg-type]
                    .selectinload(Face.room)  # type: ignore[arg-type]
                    .selectinload(Room.project),  # type: ignore[arg-type]
                    selectinload(Element.face).selectinload(Face.elements),  # type: ignore[arg-type]
                    selectinload(Element.room).selectinload(Room.project),  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    found = {
        row.id or 0: row for row in rows if row.anchor_room.project_id == project_id
    }
    if wanted - found.keys():
        raise _NOT_FOUND
    return found


def _refused(index: int, operation: BatchOperation, detail: str) -> str:
    return (
        f"opération n° {index + 1} ({operation.op}) refusée : {detail}. "
        "Le lot entier est annulé, rien n'a été écrit."
    )


async def _apply_batch_operation(
    session: SessionDep,
    project_id: int,
    operation: BatchOperation,
    faces: dict[int, Face],
    rooms: dict[int, Room],
    elements: dict[int, Element],
) -> BatchOperationResult:
    """Applique **une** opération du lot, sans jamais valider la transaction.

    Le commit unique est la raison d'être de la route (spec §10, A6) : commiter ici rendrait le
    lot partiellement applicable, donc impossible à reconstituer pour le client après un refus.
    """
    if isinstance(operation, CreateFaceElementOp):
        face = faces[operation.face_id]
        values = operation.element.model_dump()
        _reject_incoherent_element(operation.element.kind, values)
        element = Element(**values, face_id=operation.face_id)
        _reject_invalid_placement(element, face, face.room)
        session.add(element)
        await session.flush()
        return BatchOperationResult(
            op=operation.op, status="created", element_id=element.id
        )

    if isinstance(operation, CreateRoomElementOp):
        room = rooms[operation.room_id]
        element = Element(**operation.element.model_dump(), room_id=operation.room_id)
        _reject_invalid_placement(element, None, room)
        session.add(element)
        await session.flush()
        return BatchOperationResult(
            op=operation.op, status="created", element_id=element.id
        )

    if isinstance(operation, UpdateElementOp):
        element = elements[operation.element_id]
        changes = _element_changes(operation.changes)
        merged = {field: getattr(element, field) for field in FURNITURE_ONLY_FIELDS} | changes
        _reject_incoherent_element(changes.get("kind") or element.kind, merged)
        _reject_misplaced_anchor_fields(element, changes)
        for field, value in changes.items():
            setattr(element, field, value)
        _reject_invalid_placement(element, element.face, element.anchor_room)
        await session.flush()
        return BatchOperationResult(
            op=operation.op, status="updated", element_id=element.id
        )

    if isinstance(operation, DeleteElementOp):
        await _delete_row(session, Element, operation.element_id)
        return BatchOperationResult(
            op=operation.op, status="deleted", element_id=operation.element_id
        )

    if isinstance(operation, CreateRoomOp):
        room = Room(**operation.room.model_dump(), project_id=project_id)
        session.add(room)
        await session.flush()
        await sync_room_faces(session, room)
        return BatchOperationResult(op=operation.op, status="created", room_id=room.id)

    if isinstance(operation, UpdateRoomOp):
        room = rooms[operation.room_id]
        changes = operation.changes.model_dump(exclude_unset=True, exclude={"force"})
        for field, value in changes.items():
            setattr(room, field, value)
        if RESYNCHRONIZING_FIELDS & changes.keys():
            await sync_room_faces(session, room, force=operation.changes.force)
        await session.flush()
        return BatchOperationResult(op=operation.op, status="updated", room_id=room.id)

    await _delete_row(session, Room, operation.room_id)
    return BatchOperationResult(
        op=operation.op, status="deleted", room_id=operation.room_id
    )


async def _hydrate_batch_results(
    session: SessionDep, results: list[BatchOperationResult]
) -> None:
    """Recharge les objets créés ou modifiés, **après** le commit et en deux requêtes.

    Les recharger dans la boucle rendrait le lot quadratique — exactement le N+1 que la route
    existe pour supprimer. Une suppression n'a rien à recharger : son identifiant, répété dans le
    résultat, est tout ce qu'il reste à en dire.
    """
    pending = [result for result in results if result.status != "deleted"]

    element_ids = {result.element_id for result in pending if result.element_id is not None}
    if element_ids:
        rows = (
            (await session.execute(select(Element).where(col(Element.id).in_(element_ids))))
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        for result in pending:
            row = by_id.get(result.element_id)
            if row is not None:
                result.element = ElementRead.model_validate(row)

    room_ids = {result.room_id for result in pending if result.room_id is not None}
    if room_ids:
        loaded = (
            (
                await session.execute(
                    select(Room).where(col(Room.id).in_(room_ids)).options(*FULL_ROOM)
                )
            )
            .scalars()
            .all()
        )
        rooms_by_id = {entry.id: entry for entry in loaded}
        for result in pending:
            entry = rooms_by_id.get(result.room_id)
            if entry is not None:
                result.room = RoomRead.model_validate(entry)


@router.post(
    "/projects/{project_id}/batch",
    response_model=BatchResponse,
    responses=CONFLICT_RESPONSE,
)
async def apply_batch(
    project_id: int, payload: BatchRequest, session: SessionDep, current_user: CurrentUser
) -> BatchResponse:
    """Applique un lot d'écritures du plan en une transaction (spec §10, amendement A6).

    Le verrouillage optimiste sérialise les écritures : chacune incrémente `Project.version` et
    périme celle que le client détient. Déplacer quinze meubles imposait donc quinze allers-retours
    strictement séquentiels, ce qui rend un glisser-déposer inutilisable. Ici, **une** version en
    tête, **une** incrémentation, **un** commit.

    Tout ou rien : la première opération refusée annule le lot entier et nomme son rang. Un lot
    à moitié appliqué laisserait le client dans un état qu'il ne peut pas reconstituer — c'est
    exactement la perte silencieuse que la spec §8 écarte.

    Les identifiants du corps sont **tous** revérifiés contre le projet de l'URL : l'appartenance
    vérifiée une fois ne dit rien de ce que le client a mis dans sa charge utile.

    Les opérations sont appliquées **dans l'ordre d'envoi**, et aucune ne peut désigner ce qu'une
    autre vient de créer. Désigner ce qu'une opération précédente a supprimé — modifier un élément
    d'une pièce effacée plus haut dans le même lot — rend un 409 : la ligne visée n'existe plus, et
    c'est la base qui le constate. Ordonnez le lot du plus fin au plus large.
    """
    project = await get_owned_project(
        session, project_id, current_user, OrganizationRole.EDITOR
    )

    # Toutes les lectures d'abord, comme sur les routes unitaires : après `_claim_project`, le
    # projet est marqué modifié et le moindre autoflush remonterait une collision en 500.
    faces = await _batch_faces(session, project_id, payload.operations)
    rooms = await _batch_rooms(session, project_id, payload.operations)
    elements = await _batch_elements(session, project_id, payload.operations)
    await _check_furniture_types(session, _batch_furniture_type_ids(payload.operations))

    current_version = project.version
    await _claim_project(session, project, payload.version)

    results: list[BatchOperationResult] = []
    for index, operation in enumerate(payload.operations):
        try:
            results.append(
                await _apply_batch_operation(
                    session, project_id, operation, faces, rooms, elements
                )
            )
        except HTTPException as exc:
            await session.rollback()
            raise HTTPException(
                status_code=exc.status_code, detail=_refused(index, operation, str(exc.detail))
            ) from exc
        except FaceRemovalWouldLoseElements as exc:
            await session.rollback()
            raise PlanConflict(
                _refused(index, operation, str(exc))
                + " Renvoyez le lot avec `force: true` sur cette opération pour confirmer.",
                current_version=current_version,
                code="destructive_change",
            ) from exc

    await _commit_or_conflict(session, project)
    await _hydrate_batch_results(session, results)
    return BatchResponse(version=project.version, results=results)


def _batch_furniture_type_ids(operations: list[BatchOperation]) -> set[int | None]:
    """Recettes référencées par tout le lot, pour n'interroger le catalogue qu'une fois."""
    wanted: set[int | None] = set()
    for operation in operations:
        if isinstance(operation, CreateFaceElementOp | CreateRoomElementOp):
            wanted.add(operation.element.furniture_type_id)
        elif isinstance(operation, UpdateElementOp):
            fields = operation.changes.model_fields_set
            if "furniture_type_id" in fields:
                wanted.add(operation.changes.furniture_type_id)
    return wanted
