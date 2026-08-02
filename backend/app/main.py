"""Point d'entrée FastAPI.

P0 : uniquement le health check. Les routes métier arrivent en P3.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin import mount_admin
from app.api.auth import router as auth_router
from app.api.exports import router as exports_router
from app.api.furniture import router as furniture_router
from app.api.health import router as health_router
from app.api.plan import router as plan_router
from app.api.scene import router as scene_router
from app.api.share import router as share_router
from app.core.config import get_settings
from app.core.security_headers import SecurityHeadersMiddleware, add_cache_control


def create_app() -> FastAPI:
    settings = get_settings()
    # La documentation interactive est fermée hors développement : elle décrit l'intégralité de
    # la surface d'attaque et n'a aucune utilité pour un utilisateur final. Le schéma reste
    # généré en interne, et versionné dans le dépôt pour le frontend.
    expose_docs = settings.is_development

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Le schéma OpenAPI est la source de vérité du frontend
        # (docs/plan-generation-ia.md §6).
        openapi_url="/openapi.json" if expose_docs else None,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
    )

    app.add_middleware(SecurityHeadersMiddleware, https_only=not settings.is_development)
    app.middleware("http")(add_cache_control)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # Liste explicite plutôt que `*` : avec `allow_credentials`, un joker est refusé par les
        # navigateurs, et l'énumération documente ce que le frontend utilise réellement.
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Current-Version", "X-Cache", "X-Generation-Ms"],
        max_age=600,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(plan_router)
    app.include_router(furniture_router)
    app.include_router(scene_router)
    app.include_router(share_router)
    app.include_router(exports_router)
    mount_admin(app)
    return app


app = create_app()
