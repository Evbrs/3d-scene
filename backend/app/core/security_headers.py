"""En-têtes de sécurité HTTP (durcissement, ticket P12).

FastAPI n'a pas d'équivalent de `helmet` : ces en-têtes sont posés explicitement. Chacun répond à
une classe d'attaque précise, et le commentaire dit laquelle — un en-tête qu'on ne sait pas
justifier finit par être supprimé « parce qu'il gêne ».
"""

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
                for name, value in self._headers_for(path):
                    headers.append((name.encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _headers_for(self, path: str) -> list[tuple[str, str]]:
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

        if self.https_only:
            # Un an, sous-domaines inclus. Posé uniquement hors développement : en local, le
            # service est en clair, et HSTS y bloquerait le navigateur pour un an.
            headers.append(
                ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            )

        return headers


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    """Réponse 500 générique, passant par la pile de middlewares.

    Sans ce gestionnaire, `ServerErrorMiddleware` répond depuis l'extérieur de la pile : la
    réponse d'erreur sort alors **sans aucun en-tête de sécurité**. Le corps reste volontairement
    muet : un détail d'exception peut contenir un chemin, une requête SQL ou une valeur métier.
    """
    from starlette.responses import JSONResponse

    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur"})


async def add_cache_control(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Interdit la mise en cache des réponses authentifiées par un intermédiaire partagé."""
    response = await call_next(request)
    if request.headers.get("authorization") or request.url.path.startswith("/admin"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
