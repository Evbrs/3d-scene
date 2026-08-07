"""Contrat de conflit d'écriture (`docs/spec-complete.md` §8, cas 3).

Un 409 est la seule réponse que le client doit savoir **rejouer**. Encore faut-il qu'il puisse
distinguer les deux natures de conflit sans lire le message en français : c'est le rôle du champ
`code` (`stale_version` / `destructive_change`), et c'est pour ça que le corps du 409 est produit
ici, en un seul endroit, plutôt que par une `HTTPException` par route.

Le gestionnaire est branché par une classe de route : `StaleDataError` peut être levée par
n'importe quel `flush` — y compris un autoflush déclenché par une simple lecture au milieu d'une
route — et remontait alors en 500 depuis les modules qui ne l'attrapaient pas. `route_class`
couvre toutes les routes des routeurs qui l'adoptent.

`stale_data_handler` double cette protection au niveau de l'application (voir `app/main.py`) :
sans lui, un routeur ajouté plus tard sans `route_class=ConflictAwareRoute` retomberait
silencieusement sur la 500 que ce module existe pour supprimer.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import SESSION_STATE_ATTRIBUTE
from app.schemas.plan import ConflictCode, ConflictDetail

STALE_MESSAGE = "Le projet a été modifié entre-temps"


class PlanConflict(Exception):
    """Écriture refusée parce qu'elle entrerait en collision avec l'état du plan."""

    def __init__(
        self,
        detail: str = STALE_MESSAGE,
        *,
        current_version: int | None = None,
        code: ConflictCode = "stale_version",
    ) -> None:
        self.detail = detail
        self.current_version = current_version
        self.code = code
        super().__init__(detail)

    def to_response(self) -> JSONResponse:
        """409 complet : corps **et** en-tête.

        L'en-tête `X-Current-Version` est conservé parce qu'il est déjà exposé par CORS et lu par
        des clients existants ; le corps est ce que déclare l'OpenAPI, et qui ne l'était jamais.
        """
        body = ConflictDetail(
            detail=self.detail, current_version=self.current_version, code=self.code
        )
        headers = (
            {"X-Current-Version": str(self.current_version)}
            if self.current_version is not None
            else None
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=body.model_dump(),
            headers=headers,
        )


class ConflictAwareRoute(APIRoute):
    """Traduit `PlanConflict` et `StaleDataError` en 409 conforme au contrat."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except PlanConflict as conflict:
                return conflict.to_response()
            except StaleDataError:
                # Collision détectée par la base elle-même : la ligne a changé entre la lecture et
                # l'écriture. On ne sait pas ici de quel projet il s'agit, d'où l'absence de
                # version — le client recharge, c'est de toute façon la seule issue.
                await _rollback(request)
                return PlanConflict(STALE_MESSAGE).to_response()

        return guarded


async def stale_data_handler(request: Request, exc: Exception) -> Response:
    """Même traduction, branchée sur l'application entière.

    `ConflictAwareRoute` reste le chemin nominal — elle seule voit la requête assez tôt pour que
    la route puisse encore décider. Ce gestionnaire est le filet : il rattrape les routeurs qui
    n'ont pas adopté la classe de route, pour qu'une collision de version n'y devienne jamais une
    500 muette.
    """
    await _rollback(request)
    return PlanConflict(STALE_MESSAGE).to_response()


async def _rollback(request: Request) -> None:
    session: AsyncSession | None = getattr(request.state, SESSION_STATE_ATTRIBUTE, None)
    if session is not None:
        await session.rollback()
