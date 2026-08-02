"""Configuration applicative, lue depuis l'environnement.

Aucun secret en dur : tout vient de variables d'environnement (voir `env.example` à la racine).
"""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Valeur sentinelle : sa présence hors développement fait échouer le démarrage.
DEV_SECRET_KEY = "cle-de-developpement-a-remplacer-absolument-32+"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Éditeur de plan de rénovation 2D → 3D"
    # Défaut volontairement « production » : un déploiement qui oublie ENVIRONMENT doit échouer
    # au démarrage plutôt que signer jetons et cookies avec la clé de développement, qui est
    # publique puisqu'elle est dans le dépôt. Le développement l'affirme explicitement
    # (docker-compose.yml, env.example).
    environment: str = "production"
    debug: bool = False

    # Postgres (spec §6)
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"

    # Répertoire des exports générés (P9). Monté en volume dans docker-compose.
    export_dir: str = "/tmp/renovation-exports"

    # Cache du scene graph (spec §8, cas 6). Désactivable pour mesurer le gain — et désactivé
    # par défaut dans les tests, qui n'ont pas de Redis.
    cache_enabled: bool = True

    # Bascule Celery en exécution immédiate : les tests tournent ainsi sans broker.
    celery_eager: bool = False

    # Journalisation SQL — sert à mesurer les N+1 avant de les optimiser (spec §8, cas 4).
    sql_echo: bool = False

    # Clé de signature des jetons JWT. Valeur de développement uniquement : hors développement,
    # une clé faible fait échouer le démarrage (voir le validateur ci-dessous).
    secret_key: str = DEV_SECRET_KEY

    # Durées de vie des jetons (spec §6, auth JWT). Un jeton d'accès court limite la fenêtre
    # d'exploitation en cas de vol ; le jeton de rafraîchissement porte la session longue.
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Redis / Celery (spec §6, utilisés à partir de P9)
    redis_url: str = "redis://localhost:6379/0"

    # Origines autorisées. Acceptées en JSON (`["https://a", "https://b"]`) **ou** en liste
    # séparée par des virgules : `pydantic-settings` n'accepte nativement que le JSON, et
    # `CORS_ORIGINS=https://exemple.fr` — la forme qu'on écrit spontanément — ferait échouer le
    # démarrage.
    # `NoDecode` est indispensable : sans lui, `pydantic-settings` tente un `json.loads` sur la
    # valeur d'environnement **avant** d'appeler le validateur, et lève `SettingsError` sur
    # `CORS_ORIGINS=https://exemple.fr`. Le validateur ci-dessous ne s'exécutait donc jamais
    # depuis l'environnement — c'est-à-dire dans le seul cas qui compte en production.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            import json

            parsed: object = json.loads(text)
            return parsed
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @model_validator(mode="after")
    def _reject_weak_secret_outside_development(self) -> "Settings":
        """Interdit de démarrer en production avec la clé de développement.

        Le seuil de 32 octets est celui de la RFC 7518 §3.2 pour HMAC-SHA256 : en dessous, PyJWT
        lui-même émet un `InsecureKeyLengthWarning`.
        """
        if self.is_development:
            return self
        if self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32:
            raise ValueError(
                "SECRET_KEY doit être définie et faire au moins 32 caractères "
                f"hors développement (ENVIRONMENT={self.environment!r})."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Instance unique de configuration (mise en cache pour éviter les relectures disque)."""
    return Settings()
