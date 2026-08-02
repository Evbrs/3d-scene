"""API CRUD du plan 2D (`docs/spec-complete.md` §7, phase P3).

Toutes les routes sont authentifiées et passent par les permissions objet
(`app/api/permissions.py`) : aucun identifiant fourni par le client n'est utilisé sans
revérifier le propriétaire.
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import (
    get_owned_element,
    get_owned_face,
    get_owned_project,
    get_owned_room,
)
from app.models.base import utcnow
from app.models.plan import Element, Face, FurnitureType, Project, Room
from app.schemas.plan import (
    ConflictDetail,
    ElementCreate,
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
    RoomRead,
    RoomUpdate,
)
from app.services.faces import (
    FaceRemovalWouldLoseElements,
    element_fits_on_face,
    sync_room_faces,
)

router = APIRouter(prefix="/api", tags=["plan"])

# Le 409 est documenté dans l'OpenAPI : c'est la source de vérité du frontend
# (`docs/plan-generation-ia.md` §6), un conflit non déclaré y serait invisible.
CONFLICT_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ConflictDetail,
        "description": "Le plan a été modifié entre-temps (verrouillage optimiste, spec §8).",
    }
}


def _conflict(project: Project) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Le projet a été modifié entre-temps",
        headers={"X-Current-Version": str(project.version)},
    )


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
    activement édité en tête de la liste triée par `updated_at`.
    """
    if client_version is not None and client_version != project.version:
        raise _conflict(project)
    project.updated_at = utcnow()

# Chargement en une passe de l'arbre complet : sans ça, sérialiser un projet déclenche une
# requête par pièce, par face et par élément (spec §8, cas 4 — N+1).
FULL_TREE = selectinload(Project.rooms).options(  # type: ignore[arg-type]
    selectinload(Room.faces).options(selectinload(Face.elements))  # type: ignore[arg-type]
)


async def _load_full_project(session: SessionDep, project_id: int) -> Project:
    return (
        await session.execute(
            select(Project).where(col(Project.id) == project_id).options(FULL_TREE)
        )
    ).scalar_one()


# --- Projets ----------------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, current_user: CurrentUser
) -> Project:
    project = Project(**payload.model_dump(), owner_id=current_user.id or 0)
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
    """Liste paginée des projets de l'utilisateur.

    Vue résumée : charger l'arbre complet de chaque projet pour une liste serait un gâchis
    proportionnel à la taille des plans.
    """
    owned = col(Project.owner_id) == current_user.id
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
    project = await get_owned_project(session, project_id, current_user)

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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le projet a été modifié entre-temps",
            headers={"X-Current-Version": str(current_version)},
        ) from exc


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    project = await get_owned_project(session, project_id, current_user)
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Pièces -----------------------------------------------------------------------------------


async def _load_full_room(session: SessionDep, room_id: int) -> Room:
    return (
        await session.execute(
            select(Room)
            .where(col(Room.id) == room_id)
            .options(selectinload(Room.faces).options(selectinload(Face.elements)))  # type: ignore[arg-type]
        )
    ).scalar_one()


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
    project = await get_owned_project(session, project_id, current_user)

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
    room = await get_owned_room(session, room_id, current_user)
    await _claim_project(session, room.project, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version", "force"})
    for field, value in changes.items():
        setattr(room, field, value)
    room.updated_at = utcnow()

    if "polygon" in changes:
        try:
            await sync_room_faces(session, room, force=payload.force)
        except FaceRemovalWouldLoseElements as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{exc}. Renvoyez la requête avec `force: true` pour confirmer la "
                    "suppression."
                ),
            ) from exc

    await _commit_or_conflict(session, room.project)
    return await _load_full_room(session, room_id)


@router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room_id: int, session: SessionDep, current_user: CurrentUser) -> Response:
    room = await get_owned_room(session, room_id, current_user)
    project = room.project
    await session.delete(room)
    await _claim_project(session, project, None)
    await session.commit()
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
    face = await get_owned_face(session, face_id, current_user)
    await _claim_project(session, face.room.project, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "covering" in changes:
        # `covering: null` efface explicitement le revêtement ; ne pas envoyer le champ ne
        # change rien. Confondre les deux rendait l'effacement impossible.
        covering = changes["covering"] or {}
        face.covering = {key: value for key, value in covering.items() if value is not None}
    face.updated_at = utcnow()
    await _commit_or_conflict(session, face.room.project)

    return (
        await session.execute(
            select(Face)
            .where(col(Face.id) == face_id)
            .options(selectinload(Face.elements))  # type: ignore[arg-type]
        )
    ).scalar_one()


# --- Éléments ---------------------------------------------------------------------------------


def _reject_if_out_of_bounds(element: Element, face: Face) -> None:
    """Refuse un élément qui déborde de sa face.

    Sans ce contrôle, une fenêtre posée à 350 cm sur un mur long de 180 cm est acceptée, et c'est
    le calcul du scene graph (P6) qui produit une géométrie absurde — très loin du point
    d'insertion, donc très coûteuse à diagnostiquer.
    """
    problem = element_fits_on_face(element, face, face.room)
    if problem is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)


async def _check_furniture_type(session: SessionDep, furniture_type_id: int | None) -> None:
    """Le catalogue est global : on valide l'existence, pas la propriété."""
    if furniture_type_id is None:
        return
    exists = (
        await session.execute(
            select(FurnitureType.id).where(col(FurnitureType.id) == furniture_type_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Type de mobilier inconnu : {furniture_type_id}",
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
    face = await get_owned_face(session, face_id, current_user)
    await _check_furniture_type(session, payload.furniture_type_id)
    await _claim_project(session, face.room.project, payload.version)

    element = Element(**payload.model_dump(exclude={"version"}), face_id=face_id)
    _reject_if_out_of_bounds(element, face)

    session.add(element)
    try:
        await _commit_or_conflict(session, face.room.project)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Élément invalide"
        ) from exc
    await session.refresh(element)
    return element


@router.patch(
    "/elements/{element_id}", response_model=ElementRead, responses=CONFLICT_RESPONSE
)
async def update_element(
    element_id: int, payload: ElementUpdate, session: SessionDep, current_user: CurrentUser
) -> Element:
    element = await get_owned_element(session, element_id, current_user)
    await _claim_project(session, element.face.room.project, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "furniture_type_id" in changes:
        await _check_furniture_type(session, changes["furniture_type_id"])

    for field, value in changes.items():
        setattr(element, field, value)
    _reject_if_out_of_bounds(element, element.face)
    element.updated_at = utcnow()

    await _commit_or_conflict(session, element.face.room.project)
    await session.refresh(element)
    return element


@router.delete("/elements/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element(
    element_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    element = await get_owned_element(session, element_id, current_user)
    project = element.face.room.project
    await session.delete(element)
    await _claim_project(session, project, None)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
