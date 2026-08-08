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
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app.core.cache import scene_cache
from app.core.config import get_settings
from app.core.logging import log_security_event
from app.core.security import DUMMY_PASSWORD_HASH, verify_password
from app.models import Element, Face, FurnitureType, Project, Room, SharedView, User


class PlanAwareModelView(ModelView):
    """Vue d'administration qui purge le cache de scène après écriture.

    Le back-office modifie les lignes `room`, `face` et `element` **sans** passer par l'API, donc
    sans incrémenter `Project.version` : l'invalidation par version ne le couvre pas. Prétendre
    l'inverse laisserait servir une scène périmée pendant une heure après toute correction faite
    ici.

    La purge est volontairement large — toutes les versions de tous les projets touchés — parce
    qu'une purge trop fine serait plus facile à se tromper qu'à maintenir.
    """

    async def after_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        _log_admin_write("created" if is_created else "updated", model, request)
        await purge_scene_cache_for(model)

    async def after_model_delete(self, model: Any, request: Request) -> None:
        _log_admin_write("deleted", model, request)
        await purge_scene_cache_for(model)


def _log_admin_write(action: str, model: Any, request: Request) -> None:
    """Trace toute écriture du back-office.

    Ce sont les seules modifications qui contournent l'API, donc les seules dont il ne reste
    aucune trace ailleurs : sans ce journal, une correction faite à la main sur une donnée de
    client est indistinguable d'une corruption.
    """
    log_security_event(
        "admin.write",
        client_host=request.client.host if request.client else None,
        action=action,
        model=type(model).__name__,
        row_id=getattr(model, "id", None),
        admin_user_id=request.session.get("admin_user_id"),
    )


async def purge_scene_cache_for(model: Any) -> None:
    """Retrouve le projet concerné par une ligne modifiée, et purge son cache."""
    project_id = _project_id_of(model)
    if project_id is not None:
        await scene_cache.forget_project(project_id)


def _project_id_of(model: Any) -> int | None:
    """Remonte de n'importe quelle ligne du plan jusqu'à son projet."""
    if isinstance(model, Project):
        return model.id
    if isinstance(model, Room):
        return model.project_id
    with Session(sync_engine()) as session:
        if isinstance(model, Face):
            room = session.get(Room, model.room_id)
            return room.project_id if room else None
        if isinstance(model, Element):
            face = session.get(Face, model.face_id)
            room = session.get(Room, face.room_id) if face else None
            return room.project_id if room else None
    return None


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


class ProjectAdmin(PlanAwareModelView, model=Project):
    name = "Projet"
    name_plural = "Projets"
    icon = "fa-solid fa-folder"
    # `organization_id` figure en tête : c'est lui qui dit à quel client appartient le chantier
    # depuis la vague 2. `owner_id` reste affiché comme trace de création, mais il n'autorise plus
    # rien — trier le back-office dessus donnerait une vue fausse du cloisonnement.
    column_list: ClassVar[list[Any]] = [
        Project.id,
        Project.name,
        Project.organization_id,
        Project.owner_id,
        Project.version,
        Project.created_at,
    ]
    column_searchable_list: ClassVar[list[Any]] = [Project.name]
    column_sortable_list: ClassVar[list[Any]] = [Project.id, Project.name, Project.created_at]


class RoomAdmin(PlanAwareModelView, model=Room):
    name = "Pièce"
    name_plural = "Pièces"
    icon = "fa-solid fa-door-open"
    column_list: ClassVar[list[Any]] = [Room.id, Room.project_id, Room.name, Room.wall_thickness_cm]
    column_searchable_list: ClassVar[list[Any]] = [Room.name]


class FaceAdmin(PlanAwareModelView, model=Face):
    name = "Face"
    name_plural = "Faces"
    icon = "fa-solid fa-square"
    column_list: ClassVar[list[Any]] = [Face.id, Face.room_id, Face.label, Face.kind]


class ElementAdmin(PlanAwareModelView, model=Element):
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


class SharedViewAdmin(PlanAwareModelView, model=SharedView):
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
    le même port que l'API. La session est signée par une clé **distincte** de celle des JWT
    (`Settings.admin_session_key`) : une fuite de l'une ne doit pas livrer l'autre.
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", ""))
        password = str(form.get("password", ""))

        with Session(sync_engine()) as session:
            user = session.execute(
                select(User).where(col(User.email) == email)
            ).scalar_one_or_none()

        # Le hachage est vérifié même si le compte n'existe pas (temps de réponse constant).
        hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
        # Argon2id coûte ~35 ms de processeur, mesurés. Laissés dans la boucle d'évènements, ces
        # 35 ms bloquent **toutes** les autres requêtes du worker : c'est un déni de service que
        # n'importe qui déclenche en postant des formulaires de connexion.
        password_ok = await run_in_threadpool(verify_password, password, hashed)
        allowed = password_ok and user is not None and user.is_active and user.is_superuser
        if not allowed:
            log_security_event(
                "admin.login_failed",
                client_host=request.client.host if request.client else None,
            )
            return False

        assert user is not None  # garanti par `allowed`, mais invisible pour le vérificateur
        request.session.update({"admin_user_id": user.id})
        log_security_event(
            "admin.login",
            client_host=request.client.host if request.client else None,
            admin_user_id=user.id,
        )
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Revalide le compte à chaque requête.

        Se contenter de la présence de l'identifiant en session rendrait la session
        irrévocable : rétrograder, désactiver ou supprimer un compte ne fermerait pas les
        sessions déjà ouvertes.
        """
        user_id = request.session.get("admin_user_id")
        if not user_id:
            return False

        with Session(sync_engine()) as session:
            user = session.get(User, user_id)
            still_allowed = bool(user and user.is_active and user.is_superuser)

        if not still_allowed:
            request.session.clear()
        return still_allowed


_sync_engine_instance: Engine | None = None


def sync_engine() -> Engine:
    """Moteur synchrone dédié à l'admin, créé une seule fois.

    Il était auparavant construit **et détruit** à chaque requête du back-office : chaque page
    ouvrait donc une nouvelle connexion PostgreSQL, en payait la poignée de main, puis la jetait.
    Sous une navigation normale, cela suffit à saturer le `max_connections` du serveur.
    """
    global _sync_engine_instance
    if _sync_engine_instance is None:
        settings = get_settings()
        url = settings.database_url.replace("+aiosqlite", "").replace("sqlite+aiosqlite", "sqlite")
        _sync_engine_instance = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=settings.admin_db_pool_size,
            max_overflow=settings.admin_db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
        )
    return _sync_engine_instance


def mount_admin(app: FastAPI) -> Admin:
    """Monte l'admin sur `/admin` et y déclare une vue par modèle."""
    settings = get_settings()
    admin = Admin(
        app,
        sync_engine(),
        title="Plan de rénovation — Admin",
        authentication_backend=AdminAuth(
            secret_key=settings.admin_session_key,
            # Nom propre au back-office : le cookie de session par défaut de Starlette s'appelle
            # `session`, un nom que n'importe quelle autre application du même domaine emploie.
            session_cookie="admin_session",
            # Une heure au lieu de quatorze jours : un CRUD complet sur toutes les données du
            # service n'a aucune raison de rester ouvert pendant deux semaines.
            max_age=settings.admin_session_max_age_seconds,
            # Le cookie n'est envoyé qu'aux chemins du back-office, jamais à l'API publique.
            path="/admin",
            # `strict` et non `lax` : `lax` laisse le cookie partir sur une navigation initiée
            # par un site tiers, ce qui suffit à déclencher une action d'administration en un
            # clic depuis une page piégée.
            same_site="strict",
            https_only=not settings.is_development,
        ),
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
