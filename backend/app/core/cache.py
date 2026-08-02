"""Cache du scene graph (`docs/spec-complete.md` §8, cas 6).

L'arbitrage de la spec : « Cache Redis, invalidé à la modification du plan » — et elle ajoute que
c'est « un bon terrain pour pratiquer l'invalidation de cache, un des rares vrais problèmes
difficiles de l'informatique ».

**La clé porte la version du projet.** C'est le cœur de la conception : au lieu de supprimer une
entrée à chaque écriture — ce qui suppose de n'oublier aucun chemin d'écriture, et échoue
silencieusement dès qu'on en ajoute un — une modification change la clé. L'ancienne entrée
devient inatteignable et expire d'elle-même. Comme *toute* écriture du plan incrémente
`Project.version` (garanti par `_claim_project`, P3), l'invalidation est structurelle et non
déclarative.

Corollaire assumé : les entrées périmées occupent de la mémoire jusqu'à leur expiration. C'est le
prix d'une invalidation qu'on ne peut pas oublier de faire.
"""

import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

# Durée de vie d'une entrée. Assez longue pour servir une session d'édition, assez courte pour
# que les versions abandonnées ne s'accumulent pas.
SCENE_TTL_SECONDS = 3600

_client: Redis | None = None


def get_client() -> Redis | None:
    """Client Redis partagé, ou `None` si le cache est désactivé."""
    global _client
    settings = get_settings()
    if not settings.cache_enabled:
        return None
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def scene_key(project_id: int, version: int) -> str:
    return f"scene:{project_id}:v{version}"


class SceneCache:
    """Accès au cache, tolérant à la panne.

    Un cache indisponible doit **dégrader**, jamais casser : si Redis ne répond pas, la scène est
    recalculée. Laisser remonter l'erreur ferait d'un cache — une optimisation — un point de
    défaillance unique.
    """

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self.errors = 0

    async def get(self, project_id: int, version: int) -> dict[str, Any] | None:
        client = get_client()
        if client is None:
            return None
        try:
            raw = await client.get(scene_key(project_id, version))
        except RedisError:
            self.errors += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        parsed: dict[str, Any] = json.loads(raw)
        return parsed

    async def set(self, project_id: int, version: int, scene: dict[str, Any]) -> None:
        client = get_client()
        if client is None:
            return
        try:
            await client.set(
                scene_key(project_id, version), json.dumps(scene), ex=SCENE_TTL_SECONDS
            )
        except RedisError:
            self.errors += 1

    async def forget_project(self, project_id: int) -> int:
        """Supprime toutes les versions en cache d'un projet.

        Utilisé à la **suppression** d'un projet : là, aucune version future ne rendra les clés
        inatteignables, il faut donc les retirer explicitement.
        """
        client = get_client()
        if client is None:
            return 0
        removed = 0
        try:
            async for key in client.scan_iter(match=f"scene:{project_id}:v*"):
                removed += await client.delete(key)
        except RedisError:
            self.errors += 1
        return removed


scene_cache = SceneCache()
