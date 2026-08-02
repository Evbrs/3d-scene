"""Permissions objet.

Le principe : l'accès n'est jamais déduit d'un identifiant fourni par le client, il est toujours
revérifié contre le propriétaire de l'objet chargé depuis la base. C'est ce qui empêche la
référence directe d'objet non sécurisée (OWASP A01 : *Broken Access Control*).

Un objet appartenant à quelqu'un d'autre renvoie **404 et non 403** : répondre 403 confirmerait
au demandeur que l'objet existe, ce qui permet d'énumérer les identifiants des autres comptes.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from app.models.plan import Element, Face, Project, Room
from app.models.user import User

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable")


async def get_owned_project(session: AsyncSession, project_id: int, user: User) -> Project:
    """Charge un projet en vérifiant qu'il appartient à `user`."""
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or project.owner_id != user.id:
        raise _NOT_FOUND
    return project


async def get_owned_room(session: AsyncSession, room_id: int, user: User) -> Room:
    """Charge une pièce en remontant jusqu'au propriétaire du projet."""
    room = (
        await session.execute(
            select(Room)
            .where(Room.id == room_id)
            .options(selectinload(Room.project))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if room is None or room.project.owner_id != user.id:
        raise _NOT_FOUND
    return room


async def get_owned_face(session: AsyncSession, face_id: int, user: User) -> Face:
    face = (
        await session.execute(
            select(Face)
            .where(Face.id == face_id)
            .options(selectinload(Face.room).selectinload(Room.project))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if face is None or face.room.project.owner_id != user.id:
        raise _NOT_FOUND
    return face


async def get_owned_element(session: AsyncSession, element_id: int, user: User) -> Element:
    element = (
        await session.execute(
            select(Element)
            .where(Element.id == element_id)
            .options(
                selectinload(Element.face)  # type: ignore[arg-type]
                .selectinload(Face.room)  # type: ignore[arg-type]
                .selectinload(Room.project)  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if element is None or element.face.room.project.owner_id != user.id:
        raise _NOT_FOUND
    return element
