"""En-têtes de sécurité HTTP (durcissement, ticket P12).

FastAPI n'a pas d'équivalent de `helmet` : ces en-têtes sont posés explicitement. Chacun répond à
une classe d'attaque précise, et le commentaire dit laquelle — un en-tête qu'on ne sait pas
justifier finit par être supprimé « parce qu'il gêne ».
"""

import logging
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# La documentation interactive de FastAPI charge Swagger UI depuis un CDN et exécute du script
# en ligne : lui appliquer la CSP stricte de l'API la casserait. Elle a donc sa propre politique,
# et n'est de toute façon pas exposée en production (voir `main.py`).
DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})

# L'API ne renvoie que du JSON et des PDF : elle n'a besoin d'aucune source externe.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Le back-office SQLAdmin sert ses propres feuilles de style et scripts.
ADMIN_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def headers_for(path: str, *, https_only: bool) -> list[tuple[str, str]]:
    """En-têtes applicables à un chemin.

    Fonction de module et non méthode : le gestionnaire d'erreur non gérée en a besoin lui aussi,
    et il répond depuis un endroit que le middleware ne couvre pas (voir plus bas).
    """
    headers = [
        # Empêche le navigateur de deviner un type MIME : un JSON servi comme du HTML
        # deviendrait exécutable.
        ("X-Content-Type-Options", "nosniff"),
        # Aucune page de ce service n'a vocation à être encadrée : protège du détournement
        # de clic.
        ("X-Frame-Options", "DENY"),
        # Ne fuite pas l'URL d'origine — qui peut contenir un jeton de partage — vers un
        # tiers.
        ("Referrer-Policy", "no-referrer"),
        # Aucune fonctionnalité matérielle n'est utilisée.
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()"),
    ]

    if path.startswith("/admin"):
        headers.append(("Content-Security-Policy", ADMIN_CSP))
    elif path not in DOCS_PATHS:
        headers.append(("Content-Security-Policy", API_CSP))

    if https_only:
        # Un an, sous-domaines inclus. Posé uniquement hors développement : en local, le
        # service est en clair, et HSTS y bloquerait le navigateur pour un an.
        headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))

    return headers


class SecurityHeadersMiddleware:
    """Pose les en-têtes de sécurité sur chaque réponse."""

    def __init__(self, app: ASGIApp, *, https_only: bool) -> None:
        self.app = app
        self.https_only = https_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for name, value in headers_for(path, https_only=self.https_only):
                    headers.append((name.encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    """Réponse 500 générique, portant elle-même ses en-têtes de sécurité.

    Le corps reste volontairement muet : un détail d'exception peut contenir un chemin, une
    requête SQL ou une valeur métier.

    Les en-têtes sont posés **ici**, et non laissés au middleware. Un gestionnaire d'`Exception`
    est branché par Starlette sur `ServerErrorMiddleware`, qui est le tout premier middleware de
    la pile, donc *au-dessus* de ceux qu'on ajoute : sa réponse ne redescend jamais par
    `SecurityHeadersMiddleware`. C'est ce que montrait la trace d'appel une fois le test de
    régression réellement exécuté — il était jusqu'ici toujours ignoré, et la réponse 500 sortait
    avec `content-type` et `content-length` pour seuls en-têtes.

    Muet pour le client seulement : la trace complète part d'abord dans le journal, sans quoi ce
    gestionnaire transformerait chaque incident en information définitivement perdue.
    """
    logger.error(
        "exception non gérée",
        exc_info=exc,
        extra={"method": request.method, "path": request.url.path},
    )
    settings = get_settings()
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
        headers=dict(headers_for(request.url.path, https_only=not settings.is_development)),
    )


async def add_cache_control(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Interdit la mise en cache des réponses authentifiées par un intermédiaire partagé."""
    response = await call_next(request)
    if request.headers.get("authorization") or request.url.path.startswith("/admin"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
