"""Point d'entrée FastAPI.

L'ordre d'ajout des middlewares est significatif et non commutatif : `add_middleware` empile, donc
**le dernier ajouté est le plus externe**. De l'extérieur vers l'intérieur, la pile obtenue est :

    en-têtes de sécurité      ← doit voir toutes les réponses, y compris les refus ci-dessous
    adresse réelle du client  ← doit précéder tout ce qui décide en fonction de l'adresse
    contexte de requête       ← identifiant + journal d'accès, avec la bonne adresse
    nom d'hôte autorisé
    taille du corps
    CORS
    Cache-Control
    compression              ← doit voir la réponse complète, donc au plus près des routes
    routes
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm.exc import StaleDataError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin import mount_admin
from app.api.auth import router as auth_router
from app.api.conflicts import stale_data_handler
from app.api.exports import router as exports_router
from app.api.furniture import router as furniture_router
from app.api.health import router as health_router
from app.api.organizations import router as organizations_router
from app.api.plan import router as plan_router
from app.api.quotes import router as quotes_router
from app.api.scene import router as scene_router
from app.api.share import router as share_router
from app.api.takeoff import router as takeoff_router
from app.core.compression import SelectiveGZipMiddleware
from app.core.config import Settings, get_settings
from app.core.limits import BodySizeLimitMiddleware
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.probes import router as probes_router
from app.core.proxy import ProxyHeadersMiddleware
from app.core.security_headers import (
    SecurityHeadersMiddleware,
    add_cache_control,
    unhandled_error_handler,
)

# Noms d'hôte toujours acceptés en plus de la liste configurée. Ce ne sont pas des domaines qu'un
# attaquant peut exploiter pour de l'hameçonnage — une redirection vers `localhost` ne mène nulle
# part — et les oublier casserait la sonde de santé du conteneur, qui interroge la boucle locale.
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def allowed_hosts(settings: Settings) -> list[str]:
    if "*" in settings.allowed_hosts:
        return ["*"]
    return [*settings.allowed_hosts, *LOOPBACK_HOSTS]


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

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

    # Compression posée en tout premier, donc **au plus près des routes**. Elle doit voir la
    # réponse complète : `add_cache_control` est un middleware de style `BaseHTTPMiddleware`, qui
    # rediffuse systématiquement le corps en flux (`more_body=True`), et un flux n'est pas mis en
    # tampon pour être compressé. Placée au-dessus de lui, la compression ne s'appliquait donc à
    # aucune réponse — vérifié : `/openapi.json` sortait en clair, 54 314 octets.
    app.add_middleware(SelectiveGZipMiddleware)

    app.middleware("http")(add_cache_control)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # Liste explicite plutôt que `*` : avec `allow_credentials`, un joker est refusé par les
        # navigateurs, et l'énumération documente ce que le frontend utilise réellement.
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Current-Version", "X-Cache", "X-Generation-Ms", "X-Request-Id"],
        max_age=600,
    )

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    # Empoisonnement d'en-tête `Host` : sans cette barrière, `curl -H 'Host: evil.example'` sur
    # `/admin` obtient une redirection vers le domaine de l'attaquant, émise par une URL
    # parfaitement légitime. `www_redirect` est coupé : cette API n'a aucune raison d'émettre une
    # redirection vers une variante de son propre nom.
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=allowed_hosts(settings), www_redirect=False
    )

    app.add_middleware(RequestContextMiddleware)

    # Doit précéder — donc envelopper — tout ce qui décide en fonction de l'adresse du visiteur :
    # limitation de débit, journal d'accès, évènements de sécurité.
    app.add_middleware(ProxyHeadersMiddleware, trusted_proxies=settings.trusted_proxies)

    # Ajouté en dernier, donc **le plus externe** : c'est la seule position qui couvre aussi les
    # réponses produites par le middleware CORS lui-même (préflights `OPTIONS`) et les refus
    # émis par les middlewares ci-dessus, lesquels ne traversent jamais la pile applicative.
    app.add_middleware(SecurityHeadersMiddleware, https_only=not settings.is_development)

    # Une erreur non gérée est justement le moment où les en-têtes comptent le plus. Ce
    # gestionnaire est branché sur `ServerErrorMiddleware`, qui est **au-dessus** de toute la pile
    # ci-dessus : sa réponse n'y redescend pas, il pose donc lui-même ses en-têtes.
    app.add_exception_handler(Exception, unhandled_error_handler)

    # Filet global du contrat de conflit. Les routeurs du plan posent déjà `ConflictAwareRoute`,
    # mais un routeur ajouté plus tard sans elle laisserait `StaleDataError` — une collision de
    # version détectée par la base — remonter en 500, là où le client attend un 409 rejouable.
    app.add_exception_handler(StaleDataError, stale_data_handler)

    app.include_router(health_router)
    app.include_router(probes_router)
    app.include_router(auth_router)
    app.include_router(organizations_router)
    app.include_router(plan_router)
    app.include_router(furniture_router)
    app.include_router(scene_router)
    app.include_router(share_router)
    app.include_router(exports_router)
    app.include_router(takeoff_router)
    app.include_router(quotes_router)
    mount_admin(app)
    return app


app = create_app()
