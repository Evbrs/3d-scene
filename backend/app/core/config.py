"""Configuration applicative, lue depuis l'environnement.

Aucun secret en dur : tout vient de variables d'environnement (voir `env.example` à la racine).
"""

import hashlib
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Valeur sentinelle : sa présence hors développement fait échouer le démarrage.
DEV_SECRET_KEY = "cle-de-developpement-a-remplacer-absolument-32+"

# Bases Redis distinctes. Le cache de scène écrit beaucoup et sans plafond ; le courtier Celery,
# lui, ne doit jamais perdre un message. Partager la même base revient à laisser une session
# d'édition chargée évincer la file d'export (`docs/spec-complete.md` §8, cas 6). Séparer les
# bases ne suffit pas à isoler la mémoire — c'est le rôle d'une `maxmemory-policy` côté serveur
# Redis — mais rend l'isolation possible et la purge du cache inoffensive pour la file.
BROKER_REDIS_DB = 0
CACHE_REDIS_DB = 1
RATE_LIMIT_REDIS_DB = 2

# Origines refusées hors développement : Starlette **reflète l'origine du demandeur** dès que le
# joker est combiné à `allow_credentials`, ce qui revient à n'avoir aucune protection tout en
# ayant l'air d'en avoir une. `null` est l'origine d'un document sandboxé ou d'un `file://`.
FORBIDDEN_ORIGINS = frozenset({"*", "null"})


def _with_redis_db(url: str, database: int) -> str:
    """Même serveur Redis, autre numéro de base."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


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

    # Dimensionnement explicite du pool de connexions. Les défauts de SQLAlchemy (5 + 10) sont
    # pensés pour un processus unique ; ici l'arithmétique doit tenir face au `max_connections`
    # de PostgreSQL, qui vaut 100 par défaut :
    #     4 workers uvicorn x 10 = 40
    #   + 2 processus Celery    x 10 = 20
    #   + le moteur synchrone du back-office (2 + 3)
    #   = 65, soit une marge réelle pour psql, les migrations et la sonde de disponibilité.
    # Avec les défauts, la même pile réclamait 4x15 + 2x15 + 15 = 105 connexions : au-delà du
    # plafond, donc un effondrement sous charge et non une dégradation.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Attendre 30 s (le défaut) une connexion libre, c'est empiler les requêtes jusqu'à la
    # saturation de la boucle d'évènements. Échouer vite laisse le service répondre 500 sur une
    # requête plutôt que de ne plus répondre du tout.
    db_pool_timeout_seconds: float = 10.0
    # Une connexion recyclée régulièrement survit aux coupures silencieuses des pare-feu et des
    # bascules de PgBouncer.
    db_pool_recycle_seconds: int = 1800
    admin_db_pool_size: int = 2
    admin_db_max_overflow: int = 3

    # Répertoire des exports générés (P9). Monté en volume dans docker-compose.
    export_dir: str = "/tmp/renovation-exports"

    # Cache du scene graph (spec §8, cas 6). Désactivable pour mesurer le gain — et désactivé
    # par défaut dans les tests, qui n'ont pas de Redis.
    cache_enabled: bool = True

    # Bascule Celery en exécution immédiate : les tests tournent ainsi sans broker.
    celery_eager: bool = False

    # Journalisation SQL — sert à mesurer les N+1 avant de les optimiser (spec §8, cas 4).
    sql_echo: bool = False

    # Journalisation applicative. JSON sur stdout : c'est la seule forme qu'un collecteur sait
    # indexer sans expression régulière fragile, et stdout est la seule destination qu'un
    # conteneur n'a pas à monter.
    log_level: str = "INFO"
    log_json: bool = True

    # Clé de signature des jetons JWT. Valeur de développement uniquement : hors développement,
    # une clé faible fait échouer le démarrage (voir le validateur ci-dessous).
    secret_key: str = DEV_SECRET_KEY

    # Clé de signature des sessions du back-office. Vide par défaut : elle est alors **dérivée**
    # de `secret_key` par un condensat à domaine séparé (voir `admin_session_key`). Réutiliser
    # `secret_key` telle quelle ferait signer par le même secret les JWT de l'API et les cookies
    # de SQLAdmin : une fuite de l'un livrerait l'autre, et une rotation de l'un déconnecterait
    # l'autre sans qu'on comprenne pourquoi.
    admin_session_secret_key: str = ""
    # Le cookie de SQLAdmin vaut 14 jours par défaut. Un back-office qui donne un CRUD complet
    # sur toutes les données n'a aucune raison de garder une session ouverte deux semaines.
    admin_session_max_age_seconds: int = 3600

    # Durées de vie des jetons (spec §6, auth JWT). Un jeton d'accès court limite la fenêtre
    # d'exploitation en cas de vol ; le jeton de rafraîchissement porte la session longue.
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Redis / Celery (spec §6, utilisés à partir de P9)
    redis_url: str = "redis://localhost:6379/0"

    # Taille maximale d'un corps de requête. nginx impose déjà 10 Mo en bordure
    # (`frontend/nginx/default.conf.template`) ; la même borne est reposée ici parce que le
    # backend est aussi joignable sans passer par nginx (worker, sonde, déploiement sans bordure).
    max_request_body_bytes: int = 10 * 1024 * 1024

    # Réseaux autorisés à parler au nom d'un client via `X-Forwarded-*`. Tout en-tête venu d'une
    # autre adresse est ignoré : sans cela, n'importe quel visiteur choisit son IP et vide le seau
    # de limitation de quelqu'un d'autre. Le joker est refusé par le validateur.
    trusted_proxies: Annotated[list[str], NoDecode] = ["127.0.0.1", "::1"]

    # Noms d'hôte acceptés dans l'en-tête `Host`. `*` par défaut pour ne pas casser un
    # déploiement existant, mais toute valeur explicite referme l'empoisonnement d'en-tête Host
    # (une redirection du back-office repartait vers le domaine choisi par l'attaquant).
    allowed_hosts: Annotated[list[str], NoDecode] = ["*"]

    # Limitation de débit. `""` choisit automatiquement : Redis hors développement (plusieurs
    # workers, donc un compteur partagé est indispensable), mémoire de processus en local.
    rate_limit_backend: str = ""
    login_window_seconds: int = 300
    # Trois seaux, trois angles d'attaque distincts — voir `app/core/rate_limit.py`.
    login_attempts_per_client: int = 60
    login_attempts_per_client_and_account: int = 10
    login_attempts_per_account: int = 20

    # Origines autorisées. Acceptées en JSON (`["https://a", "https://b"]`) **ou** en liste
    # séparée par des virgules : `pydantic-settings` n'accepte nativement que le JSON, et
    # `CORS_ORIGINS=https://exemple.fr` — la forme qu'on écrit spontanément — ferait échouer le
    # démarrage.
    # `NoDecode` est indispensable : sans lui, `pydantic-settings` tente un `json.loads` sur la
    # valeur d'environnement **avant** d'appeler le validateur, et lève `SettingsError` sur
    # `CORS_ORIGINS=https://exemple.fr`. Le validateur ci-dessous ne s'exécutait donc jamais
    # depuis l'environnement — c'est-à-dire dans le seul cas qui compte en production.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", "trusted_proxies", "allowed_hosts", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            import json

            parsed: object = json.loads(text)
            return parsed
        return [item.strip() for item in text.split(",") if item.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def cache_redis_url(self) -> str:
        return _with_redis_db(self.redis_url, CACHE_REDIS_DB)

    @property
    def broker_redis_url(self) -> str:
        return _with_redis_db(self.redis_url, BROKER_REDIS_DB)

    @property
    def rate_limit_redis_url(self) -> str:
        return _with_redis_db(self.redis_url, RATE_LIMIT_REDIS_DB)

    @property
    def rate_limit_uses_redis(self) -> bool:
        """Redis dès qu'il y a plusieurs processus, c'est-à-dire partout sauf en local."""
        if self.rate_limit_backend:
            return self.rate_limit_backend == "redis"
        return not self.is_development

    @property
    def admin_session_key(self) -> str:
        """Secret de signature des sessions du back-office.

        Dérivé de `secret_key` quand il n'est pas fourni : c'est une clé **différente**, donc la
        compromission d'un cookie d'admin ne permet pas de forger un JWT, et réciproquement. La
        chaîne de contexte est le procédé de séparation de domaine ; elle ne doit jamais changer,
        sinon toutes les sessions ouvertes tombent.
        """
        if self.admin_session_secret_key:
            return self.admin_session_secret_key
        derived = hashlib.blake2b(
            self.secret_key.encode("utf-8"),
            person=b"admin-session",
            digest_size=32,
        )
        return derived.hexdigest()

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
        if self.admin_session_secret_key and len(self.admin_session_secret_key) < 32:
            raise ValueError(
                "ADMIN_SESSION_SECRET_KEY doit faire au moins 32 caractères hors développement."
            )
        return self

    @model_validator(mode="after")
    def _reject_permissive_cors_outside_development(self) -> "Settings":
        """Un CORS permissif hors développement est une absence de protection déguisée.

        `allow_credentials=True` (le cas de cette API, qui pose un cookie de rafraîchissement)
        combiné à `*` fait renvoyer à Starlette l'origine du demandeur dans
        `Access-Control-Allow-Origin` : n'importe quel site tiers peut alors lire les réponses
        authentifiées. Le refus est au démarrage, pas dans une note de documentation.
        """
        if self.is_development:
            return self
        for origin in self.cors_origins:
            if origin in FORBIDDEN_ORIGINS:
                raise ValueError(
                    f"CORS_ORIGINS={origin!r} est refusé hors développement : combiné à "
                    "allow_credentials, il laisse n'importe quelle origine lire les réponses "
                    "authentifiées. Énumérer les domaines réels, ou laisser la liste vide en "
                    "même origine."
                )
            if not origin.startswith("https://"):
                raise ValueError(
                    f"CORS_ORIGINS={origin!r} doit être en https:// hors développement."
                )
        return self

    @model_validator(mode="after")
    def _reject_wildcard_proxy_trust(self) -> "Settings":
        """`TRUSTED_PROXIES=*` annulerait tout l'intérêt de la liste.

        Faire confiance à tout le monde pour l'en-tête `X-Forwarded-For`, c'est laisser chaque
        visiteur choisir l'adresse sous laquelle il est compté par les limiteurs de débit.
        """
        if any(entry in FORBIDDEN_ORIGINS for entry in self.trusted_proxies):
            raise ValueError(
                "TRUSTED_PROXIES n'accepte pas de joker : énumérer les adresses ou les réseaux "
                "(notation CIDR) des relais réellement placés devant l'application."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Instance unique de configuration (mise en cache pour éviter les relectures disque)."""
    return Settings()
