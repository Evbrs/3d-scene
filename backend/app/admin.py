"""Back-office SQLAdmin.

FastAPI n'a pas d'admin natif (`docs/spec-complete.md` §6) : SQLAdmin comble ce manque, en
particulier pour gérer le catalogue `FurnitureType`.

SQLAdmin travaille en synchrone sur un moteur dédié : il utilise SQLAlchemy Core directement et
ne partage pas la session asynchrone des routes.
"""

from typing import Any, ClassVar

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlmodel import col, select
from starlette.requests import Request

from app.core.config import get_settings
from app.core.security import DUMMY_PASSWORD_HASH, verify_password
from app.models import Element, Face, FurnitureType, Project, Room, SharedView, User


class UserAdmin(ModelView, model=User):
    name = "Utilisateur"
    name_plural = "Utilisateurs"
    icon = "fa-solid fa-user"
    column_list: ClassVar[list[Any]] = [User.id, User.email, User.is_active, User.is_superuser]
    column_searchable_list: ClassVar[list[Any]] = [User.email]
    # Le hachage du mot de passe ne doit apparaître ni en détail, ni dans un formulaire : le
    # back-office n'a aucune raison de l'exposer, même à un administrateur.
    column_details_exclude_list: ClassVar[list[Any]] = [User.hashed_password]
    form_excluded_columns: ClassVar[list[Any]] = [User.hashed_password]


class ProjectAdmin(ModelView, model=Project):
    name = "Projet"
    name_plural = "Projets"
    icon = "fa-solid fa-folder"
    column_list: ClassVar[list[Any]] = [
        Project.id,
        Project.name,
        Project.owner_id,
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
    # La vue est éditable — le critère d'acceptation P1 demande que *chaque* modèle le soit —
    # mais le token en est exclu : c'est un secret de partage généré côté API (P8), le modifier
    # à la main casserait silencieusement les liens déjà diffusés.
    form_excluded_columns: ClassVar[list[Any]] = [SharedView.token]


class AdminAuth(AuthenticationBackend):
    """Authentification du back-office : compte superutilisateur uniquement.

    Sans ce garde-fou, `/admin` expose un CRUD complet sur toutes les données, sans jeton, sur
    le même port que l'API. La session est signée avec `SECRET_KEY` — la même clé dont le
    démarrage exige qu'elle soit forte hors développement.
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", ""))
        password = str(form.get("password", ""))

        engine = _sync_engine()
        try:
            with Session(engine) as session:
                user = session.execute(
                    select(User).where(col(User.email) == email)
                ).scalar_one_or_none()
        finally:
            engine.dispose()

        # Le hachage est vérifié même si le compte n'existe pas (temps de réponse constant).
        hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
        if not verify_password(password, hashed):
            return False
        if user is None or not user.is_active or not user.is_superuser:
            return False

        request.session.update({"admin_user_id": user.id})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("admin_user_id"))


def _sync_engine() -> Engine:
    """Moteur synchrone dédié à l'admin (SQLAdmin ne consomme pas le moteur async)."""
    settings = get_settings()
    url = settings.database_url.replace("+aiosqlite", "").replace("sqlite+aiosqlite", "sqlite")
    return create_engine(url, pool_pre_ping=True)


def mount_admin(app: FastAPI) -> Admin:
    """Monte l'admin sur `/admin` et y déclare une vue par modèle."""
    settings = get_settings()
    admin = Admin(
        app,
        _sync_engine(),
        title="Plan de rénovation — Admin",
        authentication_backend=AdminAuth(secret_key=settings.secret_key),
    )
    for view in (
        UserAdmin,
        ProjectAdmin,
        RoomAdmin,
        FaceAdmin,
        ElementAdmin,
        FurnitureTypeAdmin,
        SharedViewAdmin,
    ):
        admin.add_view(view)
    return admin
