"""Sondes de vivacité et de disponibilité.

Deux sondes, parce qu'elles répondent à deux questions différentes et que les confondre coûte cher
en production :

- **vivacité** (`/health/live`) : « ce processus est-il encore capable de répondre ? ». Constante,
  sans dépendance. Si elle interrogeait la base, une coupure momentanée de PostgreSQL ferait
  redémarrer en boucle des processus parfaitement sains, et la panne durerait plus longtemps que
  la cause.
- **disponibilité** (`/health/ready`) : « ce processus peut-il servir du trafic maintenant ? ».
  Elle vérifie les dépendances, et un orchestrateur le retire alors de la rotation sans le tuer.

Les deux vérifications sont sous délai dur : une sonde qui attend indéfiniment une base bloquée
n'est plus une sonde, c'est une requête de plus qui s'accumule.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])

# Une seconde : au-delà, la dépendance est de toute façon inutilisable pour servir une requête.
PROBE_TIMEOUT_SECONDS = 1.0


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    # Un composant par clé, avec la raison en clair : sans elle, un 503 n'apprend rien à
    # l'astreinte, qui doit alors deviner laquelle des dépendances est tombée.
    checks: dict[str, str]


# `include_in_schema=False` sur les deux sondes : elles s'adressent à l'orchestrateur, pas au
# frontend. Les publier dans le schéma OpenAPI — qui est le contrat du client web, régénéré et
# comparé en CI — laisserait croire qu'un navigateur a une raison de les appeler, et exposerait le
# détail des dépendances à qui n'a pas à le connaître.
@router.get("/live", response_model=LivenessResponse, include_in_schema=False)
async def liveness() -> LivenessResponse:
    """Constante par construction : elle ne dit rien d'autre que « le processus répond »."""
    return LivenessResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    include_in_schema=False,
)
async def readiness(response: Response) -> ReadinessResponse:
    """Vérifie les dépendances et nomme celle qui manque."""
    checks = {
        "database": await _check_database(),
        "redis": await _check_redis(),
    }
    degraded = [name for name, result in checks.items() if result != "ok"]
    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", checks=checks)
    return ReadinessResponse(status="ok", checks=checks)


async def _check_database() -> str:
    from app.db import get_engine

    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
    except TimeoutError:
        return "délai dépassé"
    except Exception as exc:
        # Volontairement large : une sonde de disponibilité doit nommer la panne, pas la trier.
        # Laisser remonter une exception ferait répondre 500 là où le contrat est 503.
        return type(exc).__name__
    return "ok"


async def _check_redis() -> str:
    from app.core.cache import get_client

    if not get_settings().cache_enabled:
        # Redis est optionnel quand le cache est coupé : le déclarer en panne rendrait le
        # service indisponible alors qu'il sait parfaitement répondre sans lui.
        return "ok"

    client = get_client()
    if client is None:
        return "ok"
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            await client.ping()
    except TimeoutError:
        return "délai dépassé"
    except (RedisError, OSError) as exc:
        return type(exc).__name__
    return "ok"
