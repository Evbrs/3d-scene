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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Le schéma OpenAPI est la source de vérité du frontend
        # (docs/plan-generation-ia.md §6).
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
