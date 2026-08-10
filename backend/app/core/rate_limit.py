"""Limitation de débit à fenêtre glissante.

Protège la connexion, l'inscription et les routes coûteuses contre le bourrage d'identifiants et
l'épuisement de ressources. Les premières comptent par identité visée (voir `login_buckets`), les
secondes par adresse et par nature de calcul (voir `COSTLY_QUOTAS` et `RateLimited`).

**Pourquoi Redis.** L'implémentation précédente vivait dans la mémoire du processus, alors que la
production tourne avec quatre workers uvicorn : le plafond réel était multiplié par quatre, et
remis à zéro à chaque redémarrage. Le compteur vit donc dans Redis, incrémenté par un script Lua
— la seule façon d'obtenir « purger la fenêtre, compter, décider, enregistrer » en une opération
atomique. Un `ZCARD` suivi d'un `ZADD` depuis quatre processus laisse passer quatre requêtes de
plus à chaque tour.

**Le repli est plus restrictif, jamais moins.** Si Redis ne répond pas, on retombe sur la mémoire
de processus. Comme chaque processus ne voit alors qu'une fraction du trafic, appliquer le quota
complet le multiplierait par le nombre de workers : le quota est donc divisé par ce nombre. Une
panne de Redis dégrade le confort, elle n'ouvre pas la porte.

**Trois seaux, pas deux.** L'ancienne composition en comptait deux, mais le seau dit « par cible »
était en réalité clé sur `(IP, cible)` : 500 adresses résidentielles donnaient 5 000 essais par
tranche de cinq minutes sur un seul compte. Chaque seau répond à une attaque distincte :

- `client` — par adresse : plafonne le volume total d'une source, quelle que soit la cible ;
- `client+account` — par couple : bloque le bourrage classique depuis une seule adresse ;
- `account` — par adresse e-mail **seule** : c'est celui qui manquait, et le seul qui résiste à
  une attaque distribuée sur un compte nommé.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

# Fenêtre glissante atomique. Le tri est fait sur l'horodatage, ce qui permet de purger la partie
# expirée avant de compter — donc une vraie fenêtre glissante, et non des tranches fixes où deux
# rafales à cheval sur une frontière passent pour une seule.
#
# KEYS[1] : la clé du seau. ARGV : horodatage en millisecondes, fenêtre en millisecondes, quota,
# et un membre unique (deux évènements de même horodatage ne doivent pas se confondre dans un
# ensemble trié, qui déduplique par membre).
# Retour : {autorisé (0/1), millisecondes avant la première place libre}.
SLIDING_WINDOW_SCRIPT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local quota = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local used = redis.call('ZCARD', KEYS[1])
if used >= quota then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local wait = window
  if oldest[2] then
    wait = (tonumber(oldest[2]) + window) - now
  end
  if wait < 0 then wait = 0 end
  return {0, wait}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""

# Nombre de processus web supposés en production (`docker-compose.prod.yml` : `--workers 4`).
# Sert uniquement à resserrer le repli mémoire ; le chemin Redis n'en dépend pas.
ASSUMED_WORKER_COUNT = 4

# Plafond du nombre de clés suivies en mémoire. Sans lui, l'unique route publique du service
# offrirait un levier de saturation mémoire à qui n'a même pas de compte.
#
# Il s'applique **par seau**, et le repli en compte un par quota distinct : deux portées de même
# plafond et de même fenêtre partagent donc leur seau, seule la clé les sépare. C'est ce qui
# permet d'ajouter des portées sans multiplier la mémoire du repli — `COSTLY_QUOTAS` en compte
# une dizaine pour cinq quotas distincts.
MAX_TRACKED_KEYS = 50_000

logger = logging.getLogger(__name__)


class Quota(NamedTuple):
    """Au plus `max_events` évènements par clé sur `window_seconds`."""

    max_events: int
    window_seconds: float

    def tightened(self, divisor: int = ASSUMED_WORKER_COUNT) -> Quota:
        """Quota applicable à **un** processus quand le compteur partagé est indisponible."""
        return Quota(max(1, self.max_events // max(1, divisor)), self.window_seconds)


class Decision(NamedTuple):
    allowed: bool
    # Secondes à attendre avant que la place se libère, telles qu'annoncées dans `Retry-After`.
    retry_after: int


ALLOWED = Decision(allowed=True, retry_after=0)


@dataclass
class SlidingWindowRateLimiter:
    """Fenêtre glissante en mémoire de processus. Repli du compteur Redis, et rien d'autre."""

    max_attempts: int
    window_seconds: float
    max_keys: int = MAX_TRACKED_KEYS
    _events: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def hit(self, key: str, now: float) -> bool:
        """Enregistre une tentative. Retourne `False` si le quota est dépassé.

        `now` est injecté plutôt que lu depuis l'horloge interne : c'est ce qui rend le
        comportement testable sans `sleep`.
        """
        return self.check(key, now).allowed

    def check(self, key: str, now: float) -> Decision:
        events = self._events[key]
        horizon = now - self.window_seconds
        while events and events[0] <= horizon:
            events.popleft()

        if not events:
            # Une clé vidée est retirée : sinon le dictionnaire grossit d'une entrée par IP vue.
            self._events.pop(key, None)

        if len(events) >= self.max_attempts:
            return Decision(False, self._retry_after(events[0], now))

        if len(self._events) >= self.max_keys and key not in self._events:
            # Le balayage n'a lieu qu'au moment où le plafond est atteint : le faire à chaque
            # tentative coûterait un parcours complet du dictionnaire par requête.
            self.purge_expired(now)
            if len(self._events) >= self.max_keys:
                # Toutes les clés suivies sont actives : on refuse plutôt que de continuer à
                # grossir. Un plafond qu'on dépasse « juste cette fois » n'est pas un plafond, et
                # la mémoire d'un processus n'est pas extensible.
                return Decision(False, math.ceil(self.window_seconds))

        self._events[key].append(now)
        return ALLOWED

    def purge_expired(self, now: float) -> int:
        """Libère les clés dont la fenêtre est entièrement écoulée.

        Indispensable : la purge paresseuse de `check` ne s'applique qu'aux clés effectivement
        revisitées. Une adresse vue une fois puis jamais revue occupait sa place indéfiniment.
        """
        horizon = now - self.window_seconds
        expired = [
            key for key, events in self._events.items() if not events or events[-1] <= horizon
        ]
        for key in expired:
            self._events.pop(key, None)
        return len(expired)

    def reset(self, key: str) -> None:
        """Remet à zéro le compteur d'**une** clé."""
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()

    def _retry_after(self, oldest: float, now: float) -> int:
        return max(1, math.ceil(oldest + self.window_seconds - now))

    @property
    def tracked_keys(self) -> int:
        """Nombre de clés encore suivies (sert à vérifier l'absence de fuite mémoire)."""
        return len(self._events)


class RateLimiter:
    """Compteur partagé : Redis quand il répond, mémoire de processus sinon.

    Le client Redis est créé à la première utilisation, et jamais dans le constructeur : l'objet
    est instancié à l'import du module, bien avant qu'une boucle d'évènements existe.
    """

    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._script: Any = None
        # Un seau mémoire par quota, jamais par clé : un dictionnaire de seaux mono-clé n'aurait
        # aucun moyen de purger ses entrées expirées.
        self._memory: dict[Quota, SlidingWindowRateLimiter] = {}
        self.degraded = False

    async def check(self, key: str, quota: Quota) -> Decision:
        script = self._lua_script()
        if script is not None:
            decision = await self._check_in_redis(script, key, quota)
            if decision is not None:
                return decision
        return self._check_in_memory(key, quota, degraded=script is not None)

    async def reset(self, key: str) -> None:
        """Libère une clé (après une authentification réussie, par exemple)."""
        client = self._client()
        if client is not None:
            try:
                await client.delete(key)
            except RedisError:
                self._enter_degraded_mode("reset")
        for bucket in self._memory.values():
            bucket.reset(key)

    def clear(self) -> None:
        """Vide le repli mémoire. N'a d'effet que sur ce processus — usage de test."""
        for bucket in self._memory.values():
            bucket.clear()
        self.degraded = False

    def _client(self) -> Redis | None:
        settings = get_settings()
        if not settings.rate_limit_uses_redis:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(settings.rate_limit_redis_url, decode_responses=True)
        return self._redis

    def _lua_script(self) -> Any:
        """Script enregistré une fois : les appels suivants sont des `EVALSHA`.

        `register_script` rejoue automatiquement un `EVAL` complet si Redis a oublié l'empreinte
        (redémarrage, `SCRIPT FLUSH`), ce qui évite d'avoir à traiter `NOSCRIPT` à la main.
        """
        client = self._client()
        if client is None:
            return None
        if self._script is None:
            self._script = client.register_script(SLIDING_WINDOW_SCRIPT)
        return self._script

    async def _check_in_redis(self, script: Any, key: str, quota: Quota) -> Decision | None:
        """`None` signale que Redis n'a pas répondu, donc qu'il faut se replier."""
        now_ms = int(time.time() * 1000)
        window_ms = int(quota.window_seconds * 1000)
        try:
            raw = await script(
                keys=[key],
                # Membre unique : deux tentatives de même milliseconde se confondraient dans
                # l'ensemble trié, et la seconde ne serait jamais comptée.
                args=[now_ms, window_ms, quota.max_events, f"{now_ms}-{time.perf_counter_ns()}"],
            )
        except RedisError:
            self._enter_degraded_mode("check")
            return None

        self.degraded = False
        if bool(int(raw[0])):
            return ALLOWED
        return Decision(False, max(1, math.ceil(int(raw[1]) / 1000)))

    def _check_in_memory(self, key: str, quota: Quota, *, degraded: bool) -> Decision:
        # Le repli applique un quota resserré : ce processus ne voit qu'une fraction du trafic, et
        # lui laisser le quota complet reviendrait à multiplier le plafond par le nombre de
        # workers — exactement le défaut qu'on corrige.
        effective = quota.tightened() if degraded else quota
        bucket = self._memory.get(effective)
        if bucket is None:
            bucket = SlidingWindowRateLimiter(effective.max_events, effective.window_seconds)
            self._memory[effective] = bucket
        return bucket.check(key, time.monotonic())

    def _enter_degraded_mode(self, operation: str) -> None:
        if not self.degraded:
            self.degraded = True
            logger.warning(
                "compteur de débit indisponible, repli sur la mémoire du processus",
                extra={"operation": operation, "backend": "redis"},
            )


rate_limiter = RateLimiter()


def login_buckets(client_key: str, target_key: str) -> list[tuple[str, Quota]]:
    """Les trois seaux d'une tentative d'authentification, avec leurs quotas.

    Une seule définition, partagée par le chemin synchrone historique et le chemin asynchrone :
    faire diverger les clés ou les quotas entre les deux serait la meilleure façon de croire le
    service protégé alors qu'il ne l'est plus.
    """
    settings = get_settings()
    window = float(settings.login_window_seconds)
    return [
        (f"rl:login:client:{client_key}", Quota(settings.login_attempts_per_client, window)),
        (
            f"rl:login:pair:{client_key}|{target_key}",
            Quota(settings.login_attempts_per_client_and_account, window),
        ),
        (f"rl:login:account:{target_key}", Quota(settings.login_attempts_per_account, window)),
    ]


def pair_bucket_key(client_key: str, target_key: str) -> str:
    return f"rl:login:pair:{client_key}|{target_key}"


@dataclass
class LoginRateLimiter:
    """Composition des trois seaux décrits dans le docstring du module.

    `allow` reste synchrone et purement en mémoire : c'est le chemin encore emprunté par
    `app/api/auth.py`. `check` est le chemin partagé (Redis), à lui substituer — voir le rapport
    de lot.
    """

    per_client: SlidingWindowRateLimiter
    per_target: SlidingWindowRateLimiter
    per_account: SlidingWindowRateLimiter

    def allow(self, client_key: str, target_key: str, now: float) -> bool:
        """Vrai si la tentative est autorisée. Les trois seaux sont incrémentés.

        Les `hit` sont évalués sans court-circuit : sauter l'incrément d'un seau parce qu'un autre
        a déjà refusé laisserait une fenêtre d'attaque en alternant les cibles.
        """
        client_ok = self.per_client.hit(client_key, now)
        pair_ok = self.per_target.hit(pair_bucket_key(client_key, target_key), now)
        account_ok = self.per_account.hit(target_key, now)
        return client_ok and pair_ok and account_ok

    async def check(self, client_key: str, target_key: str) -> Decision:
        """Même décision, comptée dans Redis — donc partagée par tous les workers."""
        refused: list[int] = []
        for key, quota in login_buckets(client_key, target_key):
            # Tous les seaux sont consultés, même après un refus : s'arrêter au premier laisserait
            # les autres compteurs à zéro et rendrait l'alternance de cibles à nouveau payante.
            decision = await rate_limiter.check(key, quota)
            if not decision.allowed:
                refused.append(decision.retry_after)
        if not refused:
            return ALLOWED
        return Decision(False, max(refused))

    def reset_target(self, client_key: str, target_key: str) -> None:
        """Après une authentification réussie, seuls les seaux de *cette* cible sont libérés.

        Le seau par adresse reste intact : c'est lui qui empêche d'utiliser un succès sur son
        propre compte comme jeton de remise à zéro pour attaquer d'autres comptes.
        """
        self.per_target.reset(pair_bucket_key(client_key, target_key))
        self.per_account.reset(target_key)

    async def reset_target_async(self, client_key: str, target_key: str) -> None:
        await rate_limiter.reset(pair_bucket_key(client_key, target_key))
        await rate_limiter.reset(f"rl:login:account:{target_key}")
        self.reset_target(client_key, target_key)

    def clear(self) -> None:
        self.per_client.clear()
        self.per_target.clear()
        self.per_account.clear()
        rate_limiter.clear()


def build_login_rate_limiter() -> LoginRateLimiter:
    settings = get_settings()
    window = float(settings.login_window_seconds)
    return LoginRateLimiter(
        per_client=SlidingWindowRateLimiter(settings.login_attempts_per_client, window),
        per_target=SlidingWindowRateLimiter(
            settings.login_attempts_per_client_and_account, window
        ),
        per_account=SlidingWindowRateLimiter(settings.login_attempts_per_account, window),
    )


def client_key(request: Request) -> str:
    """Clé de limitation dérivée de l'adresse du visiteur.

    L'adresse n'est ni stockée ni journalisée telle quelle (les journaux la tronquent à son
    réseau) : elle ne sert qu'à indexer un seau en mémoire ou dans Redis.
    """
    client = request.client
    return client.host if client else "inconnu"


class RateLimited:
    """Dépendance FastAPI posée sur les routes coûteuses : `Depends(costly("scene"))`.

    La protection ne porte pas ici sur des identifiants mais sur le **temps processeur** : ces
    routes calculent, et un calcul n'a pas besoin d'être malveillant pour saturer les workers. Le
    `scope` sépare les compteurs, pour qu'une route saturée n'en ferme pas d'autres.

    La clé est l'adresse du visiteur et non le compte : la dépendance est déclarée au niveau de la
    route, donc FastAPI la résout **avant** `CurrentUser`. C'est délibéré — refuser avant d'avoir
    résolu une identité est ce qui rend le refus bon marché, et c'est la seule façon de couvrir
    aussi la vue publique, qui n'a pas d'identité à résoudre.
    """

    def __init__(self, scope: str, max_events: int, window_seconds: float) -> None:
        self.scope = scope
        self.quota = Quota(max_events, window_seconds)

    async def __call__(self, request: Request) -> None:
        key = f"rl:{self.scope}:{client_key(request)}"
        decision = await rate_limiter.check(key, self.quota)
        if decision.allowed:
            return
        # Pas de journalisation ici : `RequestContextMiddleware` transforme tout 429 en évènement
        # de sécurité, quelle que soit la route qui l'a émis. Le faire aux deux endroits
        # produirait deux lignes pour un seul refus.
        raise too_many_attempts(decision.retry_after)


# Un plafond n'est pas un nombre de requêtes « raisonnable » : c'est un budget de temps processeur
# par minute et par adresse. Les durées ci-dessous sont des médianes mesurées sur l'application
# réelle, pièce vide de 5 x 4 m avec un percement, douze appels après chauffe — la même sonde qui a
# servi à calibrer ces quotas les rejoue.
#
# Deux routes de même coût partagent délibérément un seau. Leur donner un compteur chacune
# reviendrait à doubler le budget en alternant, alors qu'elles produisent le même travail.
COSTLY_QUOTAS: dict[str, Quota] = {
    # 3,8 ms. Servie par le cache de scène la plupart du temps, et l'éditeur la redemande après
    # chaque enregistrement : c'est la lecture la plus fréquente du produit, d'où le plafond haut.
    "scene": Quota(120, 60.0),
    # 5,0 ms.
    "inspection": Quota(60, 60.0),
    # 5,2 ms.
    "laying_plan": Quota(60, 60.0),
    # 3,8 ms, JSON et CSV confondus — c'est le même calcul rendu deux fois.
    "takeoff": Quota(60, 60.0),
    # 132 ms pour une salle de bain, **633 ms** pour une cuisine accessible en cinq variantes.
    # C'est de très loin le calcul le plus cher du produit : sans plafond, une boucle sur cette
    # seule route tient les quatre workers occupés depuis un compte gratuit. Six par minute reste
    # généreux pour un humain qui demande une implantation et regarde le résultat.
    "layout": Quota(6, 60.0),
    # 8,7 ms sur une pièce, mais la durée croît avec le plan, et le chemin asynchrone occupe en
    # plus un worker Celery pour le même rendu. Les deux chemins partagent le seau.
    "export_pdf": Quota(12, 60.0),
    # Suivi de tâche et téléchargement : bon marché, mais interrogés en boucle par un client qui
    # attend son fichier.
    "export_read": Quota(120, 60.0),
    # Reconstruit le métré, donc la scène, puis applique le barème ligne à ligne.
    "quote_build": Quota(20, 60.0),
    # Rendu Factur-X : PDF/A-3 et XML CII, produits par le même chemin.
    "quote_render": Quota(20, 60.0),
    # Seule route atteignable sans compte, et chaque appel déclenche un calcul de scène complet.
    # Le plafond reprend celui du compteur mémoire qu'elle utilisait auparavant, mais il est
    # désormais **partagé** par les quatre workers au lieu d'être multiplié par quatre.
    "public_view": Quota(60, 60.0),
}


def costly(scope: str) -> RateLimited:
    """Dépendance de débit d'une route coûteuse, au tarif calibré pour son `scope`.

    Le plafond vient de `COSTLY_QUOTAS` et jamais de nombres écrits à côté du `@router.get` :
    dispersés sur les routes, ils divergent, et plus personne ne peut dire quel budget processeur
    le service accepte de céder à une adresse. Un `scope` inconnu lève ici, au chargement du
    module — donc au démarrage, et non au premier appel de la route.
    """
    quota = COSTLY_QUOTAS[scope]
    return RateLimited(scope, quota.max_events, quota.window_seconds)


def too_many_attempts(retry_after: int) -> HTTPException:
    """429 accompagnée de `Retry-After`.

    Sans cet en-tête, un client légitime ne peut que retenter à l'aveugle — donc trop tôt, donc
    en aggravant exactement la charge que la limitation cherche à contenir.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Trop de tentatives, réessayez plus tard",
        headers={"Retry-After": str(max(1, retry_after))},
    )
