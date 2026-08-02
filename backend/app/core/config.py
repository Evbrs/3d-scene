"""Configuration applicative, lue depuis l'environnement.

Aucun secret en dur : tout vient de variables d'environnement (voir `.env.example`).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Éditeur de plan de rénovation 2D → 3D"
    environment: str = "development"
    debug: bool = False

    # Postgres (spec §6)
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"

    # Redis / Celery (spec §6, utilisés à partir de P9)
    redis_url: str = "redis://localhost:6379/0"

    # Origines autorisées pour le frontend Vite en dev
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Instance unique de configuration (mise en cache pour éviter les relectures disque)."""
    return Settings()
