"""Back-office SQLAdmin.

FastAPI n'a pas d'admin natif (`docs/spec-complete.md` §6) : SQLAdmin comble ce manque, en
particulier pour gérer le catalogue `FurnitureType`.

SQLAdmin travaille en synchrone sur un moteur dédié : il utilise SQLAlchemy Core directement et
ne partage pas la session asynchrone des routes.
"""

from typing import Any, ClassVar

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqlalchemy import Engine, create_engine

from app.core.config import get_settings
from app.models import Element, Face, FurnitureType, Project, Room, SharedView


class ProjectAdmin(ModelView, model=Project):
    name = "Projet"
    name_plural = "Projets"
    icon = "fa-solid fa-folder"
    column_list: ClassVar[list[Any]] = [
        Project.id,
        Project.name,
        Project.version,
        Project.created_at,
    ]
    column_searchable_list: ClassVar[list[Any]] = [Project.name]
    column_sortable_list: ClassVar[list[Any]] = [Project.id, Project.name, Project.created_at]


class RoomAdmin(ModelView, model=Room):
    name = "Pièce"
    name_plural = "Pièces"
    icon = "fa-solid fa-door-open"
    column_list: ClassVar[list[Any]] = [Room.id, Room.project_id, Room.name, Room.wall_thickness_cm]
    column_searchable_list: ClassVar[list[Any]] = [Room.name]


class FaceAdmin(ModelView, model=Face):
    name = "Face"
    name_plural = "Faces"
    icon = "fa-solid fa-square"
    column_list: ClassVar[list[Any]] = [Face.id, Face.room_id, Face.label, Face.kind]


class ElementAdmin(ModelView, model=Element):
    name = "Élément"
    name_plural = "Éléments"
    icon = "fa-solid fa-cube"
    column_list: ClassVar[list[Any]] = [
        Element.id,
        Element.face_id,
        Element.kind,
        Element.furniture_type_id,
    ]


class FurnitureTypeAdmin(ModelView, model=FurnitureType):
    name = "Type de mobilier"
    name_plural = "Catalogue de mobilier"
    icon = "fa-solid fa-couch"
    column_list: ClassVar[list[Any]] = [
        FurnitureType.id,
        FurnitureType.slug,
        FurnitureType.name,
        FurnitureType.category,
    ]
    column_searchable_list: ClassVar[list[Any]] = [FurnitureType.slug, FurnitureType.name]


class SharedViewAdmin(ModelView, model=SharedView):
    name = "Vue partagée"
    name_plural = "Vues partagées"
    icon = "fa-solid fa-share-nodes"
    column_list: ClassVar[list[Any]] = [
        SharedView.id,
        SharedView.project_id,
        SharedView.token,
        SharedView.created_at,
    ]
    # Un token de partage ne se modifie pas à la main : il est généré côté API (P8).
    can_edit = False


def _sync_engine() -> Engine:
    """Moteur synchrone dédié à l'admin (SQLAdmin ne consomme pas le moteur async)."""
    settings = get_settings()
    url = settings.database_url.replace("+aiosqlite", "").replace("sqlite+aiosqlite", "sqlite")
    return create_engine(url, pool_pre_ping=True)


def mount_admin(app: FastAPI) -> Admin:
    """Monte l'admin sur `/admin` et y déclare une vue par modèle."""
    admin = Admin(app, _sync_engine(), title="Plan de rénovation — Admin")
    for view in (
        ProjectAdmin,
        RoomAdmin,
        FaceAdmin,
        ElementAdmin,
        FurnitureTypeAdmin,
        SharedViewAdmin,
    ):
        admin.add_view(view)
    return admin
