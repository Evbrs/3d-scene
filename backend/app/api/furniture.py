"""API du catalogue de mobilier paramétrique (`docs/spec-complete.md` §4, phase P5).

Le catalogue est **global** et non rattaché à un compte : c'est une bibliothèque de recettes
partagée. Lecture ouverte à tout utilisateur authentifié, écriture réservée aux
superutilisateurs — sans quoi n'importe quel compte pourrait modifier une recette utilisée par
les plans de tous les autres.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.conflicts import ConflictAwareRoute
from app.api.deps import CurrentUser, SessionDep, get_current_user
from app.models.base import FurnitureCategory
from app.models.plan import FurnitureType
from app.models.user import User
from app.schemas.furniture import (
    FurnitureTypeCreate,
    FurnitureTypePage,
    FurnitureTypeRead,
    FurnitureTypeUpdate,
)

router = APIRouter(
    prefix="/api/furniture-types", tags=["catalogue"], route_class=ConflictAwareRoute
)

# Caractère d'échappement du `LIKE`. Le choix d'un caractère qui n'est ni `%` ni `_` est le seul
# qui compte ; l'antislash est celui qu'attendent PostgreSQL comme SQLite par défaut.
LIKE_ESCAPE = "\\"


async def require_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Le catalogue est partagé : seul un superutilisateur peut l'écrire."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Réservé aux administrateurs du catalogue",
        )
    return current_user


SuperUser = Annotated[User, Depends(require_superuser)]


def _escape_wildcards(pattern: str) -> str:
    """Neutralise les jokers `LIKE` d'un motif saisi par l'utilisateur.

    L'antislash est échappé en premier, sinon on ré-échapperait ceux qu'on vient d'introduire.
    """
    escaped = pattern.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    return escaped.replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")


@router.get("", response_model=FurnitureTypePage)
async def list_furniture_types(
    session: SessionDep,
    current_user: CurrentUser,
    category: FurnitureCategory | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FurnitureTypePage:
    """Catalogue paginé, filtrable par catégorie et par nom."""
    statement = select(FurnitureType)
    count_statement = select(func.count()).select_from(FurnitureType)

    if category is not None:
        condition = col(FurnitureType.category) == category.value
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    if search:
        # `ilike` sur un paramètre lié : aucune concaténation de chaîne SQL. Les jokers du motif
        # sont échappés : sans ça, chercher `_` ramenait tout le catalogue et `%` en faisait un
        # scan complet déguisé en recherche.
        condition = col(FurnitureType.name).ilike(
            f"%{_escape_wildcards(search)}%", escape=LIKE_ESCAPE
        )
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)

    total = (await session.execute(count_statement)).scalar_one()
    rows = (
        (
            await session.execute(
                statement.order_by(col(FurnitureType.category), col(FurnitureType.name))
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return FurnitureTypePage(
        total=total,
        limit=limit,
        offset=offset,
        items=[FurnitureTypeRead.model_validate(row) for row in rows],
    )


@router.get("/{slug}", response_model=FurnitureTypeRead)
async def read_furniture_type(
    slug: str, session: SessionDep, current_user: CurrentUser
) -> FurnitureType:
    furniture_type = (
        await session.execute(select(FurnitureType).where(col(FurnitureType.slug) == slug))
    ).scalar_one_or_none()
    if furniture_type is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Type de mobilier introuvable"
        )
    return furniture_type


@router.post("", response_model=FurnitureTypeRead, status_code=status.HTTP_201_CREATED)
async def create_furniture_type(
    payload: FurnitureTypeCreate, session: SessionDep, _admin: SuperUser
) -> FurnitureType:
    data = payload.model_dump(mode="json")
    furniture_type = FurnitureType(**data)
    session.add(furniture_type)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un type de mobilier existe déjà pour le slug {payload.slug!r}",
        ) from exc
    await session.refresh(furniture_type)
    return furniture_type


@router.patch("/{slug}", response_model=FurnitureTypeRead)
async def update_furniture_type(
    slug: str, payload: FurnitureTypeUpdate, session: SessionDep, _admin: SuperUser
) -> FurnitureType:
    furniture_type = (
        await session.execute(select(FurnitureType).where(col(FurnitureType.slug) == slug))
    ).scalar_one_or_none()
    if furniture_type is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Type de mobilier introuvable"
        )

    changes = payload.model_dump(mode="json", exclude_unset=True)

    # Cohérence recette / emplacements couleur, y compris quand un seul des deux champs change :
    # valider isolément laisserait passer une mise à jour qui casse l'autre côté.
    slots = set(changes.get("color_slots", furniture_type.color_slots))
    parts = changes.get("parts", furniture_type.parts)
    unknown = sorted({part["color_slot"] for part in parts} - slots)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"emplacements couleur non déclarés : {', '.join(unknown)}",
        )

    for field, value in changes.items():
        setattr(furniture_type, field, value)
    await session.commit()
    await session.refresh(furniture_type)
    return furniture_type


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_furniture_type(slug: str, session: SessionDep, _admin: SuperUser) -> Response:
    """Suppression d'une entrée du catalogue.

    Les éléments qui la référencent ne sont pas supprimés : la clé étrangère est en
    `ON DELETE SET NULL` (P1), pour qu'un retrait du catalogue ne détruise pas les plans.
    """
    furniture_type = (
        await session.execute(select(FurnitureType).where(col(FurnitureType.slug) == slug))
    ).scalar_one_or_none()
    if furniture_type is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Type de mobilier introuvable"
        )

    await session.delete(furniture_type)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
