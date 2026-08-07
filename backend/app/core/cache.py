"""Cache du scene graph (`docs/spec-complete.md` §8, cas 6).

L'arbitrage de la spec : « Cache Redis, invalidé à la modification du plan » — et elle ajoute que
c'est « un bon terrain pour pratiquer l'invalidation de cache, un des rares vrais problèmes
difficiles de l'informatique ».

**La clé porte tout ce dont la scène dépend**, et pas seulement l'identifiant du projet :

- `Project.version`, incrémentée par toute écriture du plan passant par l'API (`_claim_project`,
  P3) ;
- une **empreinte du catalogue de mobilier**, parce que le scene graph développe les recettes :
  modifier une recette change toutes les scènes qui l'utilisent, sans toucher à aucun projet.

Une modification change donc la clé, l'ancienne entrée devient inatteignable et expire d'elle-
même. C'est plus robuste qu'un `delete` posé sur chaque chemin d'écriture, qu'on finit par
oublier d'ajouter en même temps qu'un nouveau chemin.

**Ce que cette approche ne couvre pas, et qui exige donc une purge explicite** :

- la suppression d'un projet — aucune version future ne viendra rendre les clés inatteignables ;
- les écritures du back-office SQLAdmin, qui modifient les lignes `room`, `face` et `element`
  sans passer par l'API. `app/admin.py` déclenche donc une purge après chaque écriture. Prétendre
  que l'invalidation par version suffit ici serait faux : le back-office ne touche pas
  `Project.version`.

Corollaire assumé : les entrées périmées occupent de la mémoire jusqu'à leur expiration. C'est le
prix d'une invalidation qu'on ne peut pas oublier de faire.
"""

import hashlib
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
        # Base Redis distincte de celle du courtier Celery : voir `app/core/config.py`. Le cache
        # accumule une entrée par (projet, version, catalogue) ; sur la base du courtier, une
        # session d'édition chargée finissait par évincer des messages de la file d'export.
        _client = Redis.from_url(settings.cache_redis_url, decode_responses=True)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def scene_key(project_id: int, version: int, catalog_fingerprint: str = "0") -> str:
    """Clé de cache d'une scène.

    `catalog_fingerprint` résume l'état des recettes de mobilier utilisées : sans lui, modifier
    une recette laisserait servir l'ancienne géométrie à tous les projets qui l'emploient,
    jusqu'à expiration.
    """
    return f"scene:{project_id}:v{version}:c{catalog_fingerprint}"


def catalog_fingerprint(furniture_types: dict[int, dict[str, Any]]) -> str:
    """Empreinte courte et stable des recettes qui participent à une scène.

    Une empreinte plutôt qu'un compteur global : elle ne dépend que des recettes réellement
    utilisées par ce projet, donc modifier une recette n'invalide que les scènes concernées.
    """
    if not furniture_types:
        return "0"
    payload = json.dumps(
        {str(key): value for key, value in sorted(furniture_types.items())},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.blake2s(payload.encode("utf-8"), digest_size=8).hexdigest()


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

    async def get(
        self, project_id: int, version: int, fingerprint: str = "0"
    ) -> dict[str, Any] | None:
        client = get_client()
        if client is None:
            return None
        try:
            raw = await client.get(scene_key(project_id, version, fingerprint))
        except RedisError:
            self.errors += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        parsed: dict[str, Any] = json.loads(raw)
        return parsed

    async def set(
        self, project_id: int, version: int, scene: dict[str, Any], fingerprint: str = "0"
    ) -> None:
        client = get_client()
        if client is None:
            return
        try:
            await client.set(
                scene_key(project_id, version, fingerprint),
                json.dumps(scene),
                ex=SCENE_TTL_SECONDS,
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
