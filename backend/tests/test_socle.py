"""Lot L2 — socle applicatif : arrêter les effondrements serveur.

Ces tests portent sur l'infrastructure, pas sur le métier : cycle de vie du moteur, bornes sur les
requêtes, confiance accordée aux relais, compteurs de débit, journalisation et sondes. Ce sont
exactement les propriétés qui ne cassent aucun test fonctionnel quand elles disparaissent — et qui
font tomber le service en production.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import QueuePool
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.core.compression import SelectiveGZipMiddleware
from app.core.config import Settings
from app.core.limits import BodySizeLimitMiddleware
from app.core.logging import (
    JsonFormatter,
    RequestContextMiddleware,
    anonymized_ip,
    current_request_id,
)
from app.core.proxy import ProxyHeadersMiddleware
from app.core.rate_limit import (
    Decision,
    Quota,
    RateLimited,
    RateLimiter,
    SlidingWindowRateLimiter,
    build_login_rate_limiter,
)

TRUSTED_PROXY = "10.9.0.7"
UNTRUSTED_PEER = "203.0.113.55"
REAL_VISITOR = "198.51.100.42"


def echo_scope(scope: Scope) -> dict[str, Any]:
    client = scope.get("client")
    return {"client": client[0] if client else None, "scheme": scope.get("scheme")}


async def scope_reporter(scope: Scope, receive: Receive, send: Send) -> None:
    """Application ASGI minimale qui renvoie ce que la pile a mis dans le scope."""
    body = json.dumps(echo_scope(scope)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def call(app: Any, scope_extra: dict[str, Any]) -> dict[str, Any]:
    """Joue une requête HTTP directement sur une application ASGI, sans serveur."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        **scope_extra,
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return {"status": start["status"], "headers": start["headers"], "body": body}


# --- Cycle de vie du moteur : `reset_engine` ne doit plus fuir de pool ------------------------


async def test_resetting_the_engine_disposes_the_pool() -> None:
    """Le défaut d'origine : le moteur était déréférencé, jamais rendu.

    Chaque appel — et il y en avait un par requête sur la route d'export direct — abandonnait un
    pool avec ses connexions PostgreSQL ouvertes. Ce test vérifie que le pool est bien rendu, en
    observant le compteur de connexions rendues du pool lui-même.
    """
    from app import db

    engine = db.get_engine()
    async with engine.connect() as connection:
        await connection.execute(__import__("sqlalchemy").text("SELECT 1"))

    pool = engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.checkedin() >= 1, "aucune connexion n'a été ouverte, le test ne prouverait rien"

    await db.reset_engine()

    assert pool.checkedin() == 0, "le pool a été abandonné sans être vidé"
    assert db.get_engine() is not engine


async def test_resetting_the_engine_is_a_coroutine() -> None:
    """Garde-fou : un `reset_engine()` synchrone redeviendrait une fuite silencieuse.

    Sans `await`, l'appel renverrait une coroutine jamais exécutée — donc aucun `dispose`, et le
    même défaut de retour, mais cette fois sans aucun symptôme visible dans le code.
    """
    from app import db

    assert asyncio.iscoroutinefunction(db.reset_engine)


async def test_the_export_task_no_longer_resets_the_engine_per_call() -> None:
    """Régression : `reset_engine` était appelé depuis `_load_project`.

    Cette fonction est empruntée par la route HTTP d'export synchrone : chaque téléchargement
    jetait donc le pool du serveur web. Le renouvellement doit se faire une fois par processus,
    au signal de démarrage du worker Celery.
    """
    import inspect

    from app.tasks import exports

    source = inspect.getsource(exports._load_project)
    assert "reset_engine" not in source

    assert hasattr(exports, "_renew_engine_after_fork")


def test_the_pool_is_sized_from_the_configuration() -> None:
    """Les défauts de SQLAlchemy (5 + 10) ne tiennent pas face à 4 workers plus Celery."""
    from app import db

    settings = Settings(environment="development")
    pool = db.get_engine().pool

    assert isinstance(pool, QueuePool)
    assert pool.size() == settings.db_pool_size
    assert settings.db_pool_size + settings.db_max_overflow <= 10


# --- Corps de requête borné -------------------------------------------------------------------


def oversized_app(max_bytes: int) -> Any:
    """Application ASGI nue derrière le middleware.

    Volontairement pas une application Starlette complète : celle-ci embarque son propre
    `ServerErrorMiddleware`, qui émettrait un 500 avant que le middleware ait pu répondre 413. Ce
    n'est pas la disposition réelle — dans `create_app`, la borne est posée sous cette couche —
    mais elle brouillerait ce que ce test mesure.
    """

    async def reader(scope: Scope, receive: Receive, send: Send) -> None:
        total = 0
        while True:
            message = await receive()
            total += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        payload = json.dumps({"lu": total}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    return BodySizeLimitMiddleware(reader, max_bytes=max_bytes)


async def post(app: Any, body: bytes, *, announce_length: bool, chunk: int = 64) -> dict[str, Any]:
    headers = [(b"content-type", b"application/json")]
    if announce_length:
        headers.append((b"content-length", str(len(body)).encode()))
    else:
        headers.append((b"transfer-encoding", b"chunked"))

    chunks = [body[index : index + chunk] for index in range(0, len(body), chunk)] or [b""]
    pending = list(chunks)

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        piece = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": piece, "more_body": bool(pending)}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body_out = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return {"status": start["status"], "body": body_out}


async def test_an_announced_oversized_body_is_refused_without_being_read() -> None:
    response = await post(oversized_app(1000), b"x" * 5000, announce_length=True)

    assert response["status"] == 413
    assert "trop volumineux" in response["body"].decode().lower()


async def test_a_chunked_oversized_body_is_refused_too() -> None:
    """Le cas qui compte : `Transfer-Encoding: chunked` n'a pas de `Content-Length`.

    Se fier au seul en-tête annoncé laisse passer exactement la forme qu'emploie un client qui
    cherche à faire allouer de la mémoire au serveur.
    """
    response = await post(oversized_app(1000), b"x" * 5000, announce_length=False)

    assert response["status"] == 413


async def test_a_normal_body_passes_through() -> None:
    response = await post(oversized_app(1000), b"x" * 200, announce_length=True)

    assert response["status"] == 200
    assert json.loads(response["body"]) == {"lu": 200}


async def test_the_running_application_really_refuses_an_oversized_body(
    client: AsyncClient,
) -> None:
    """La borne doit tenir dans la pile réelle, pas seulement en isolation.

    Elle est posée au-dessus de la compression, du CORS et du routeur : un ordre différent la
    rendrait inopérante sans qu'aucun test unitaire ne le voie.
    """
    limit = Settings(environment="development").max_request_body_bytes

    response = await client.post(
        "/api/auth/register",
        content=b"x" * (limit + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


# --- Confiance accordée aux relais ------------------------------------------------------------


async def test_a_forwarded_header_from_an_untrusted_peer_is_ignored() -> None:
    """Le test bloquant du lot.

    Sans lui, il suffit qu'un jour la liste de confiance passe à `*` — ou que le middleware
    disparaisse — pour que chaque visiteur choisisse l'adresse sous laquelle il est compté. Tous
    les seaux de limitation deviennent alors contournables en une ligne de `curl`.
    """
    app = ProxyHeadersMiddleware(scope_reporter, trusted_proxies=[TRUSTED_PROXY])

    response = await call(
        app,
        {
            "client": (UNTRUSTED_PEER, 5000),
            "headers": [(b"x-forwarded-for", REAL_VISITOR.encode())],
        },
    )

    assert json.loads(response["body"])["client"] == UNTRUSTED_PEER


async def test_a_forwarded_header_from_the_trusted_proxy_is_honoured() -> None:
    app = ProxyHeadersMiddleware(scope_reporter, trusted_proxies=["10.9.0.0/24"])

    response = await call(
        app,
        {
            "client": (TRUSTED_PROXY, 5000),
            "headers": [
                (b"x-forwarded-for", REAL_VISITOR.encode()),
                (b"x-forwarded-proto", b"https"),
            ],
        },
    )

    payload = json.loads(response["body"])
    assert payload["client"] == REAL_VISITOR
    assert payload["scheme"] == "https"


async def test_the_client_cannot_prepend_a_fake_address() -> None:
    """La partie gauche de `X-Forwarded-For` est écrite par le client, elle ne fait pas foi.

    Lire la première adresse — l'erreur classique — laisserait chaque visiteur se déclarer
    n'importe qui. Seule la plus à droite qui n'est pas un relais de confiance est le vrai pair.
    """
    app = ProxyHeadersMiddleware(scope_reporter, trusted_proxies=["10.9.0.0/24"])

    response = await call(
        app,
        {
            "client": (TRUSTED_PROXY, 5000),
            "headers": [(b"x-forwarded-for", f"1.2.3.4, {REAL_VISITOR}".encode())],
        },
    )

    assert json.loads(response["body"])["client"] == REAL_VISITOR


async def test_an_unparsable_forwarded_chain_keeps_the_peer_address() -> None:
    app = ProxyHeadersMiddleware(scope_reporter, trusted_proxies=["10.9.0.0/24"])

    response = await call(
        app,
        {
            "client": (TRUSTED_PROXY, 5000),
            "headers": [(b"x-forwarded-for", b"pas-une-adresse")],
        },
    )

    assert json.loads(response["body"])["client"] == TRUSTED_PROXY


async def test_a_forged_forwarded_proto_from_an_untrusted_peer_is_ignored() -> None:
    """Sinon un visiteur fait croire au service qu'il est servi en HTTPS alors qu'il ne l'est
    pas — et les URL absolues qu'il émet deviennent fausses."""
    app = ProxyHeadersMiddleware(scope_reporter, trusted_proxies=[TRUSTED_PROXY])

    response = await call(
        app,
        {
            "client": (UNTRUSTED_PEER, 5000),
            "headers": [(b"x-forwarded-proto", b"https")],
        },
    )

    assert json.loads(response["body"])["scheme"] == "http"


# --- Nom d'hôte -------------------------------------------------------------------------------


async def test_an_unlisted_host_header_is_refused() -> None:
    """`curl -H 'Host: evil.example'` obtenait une redirection vers le domaine de l'attaquant."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from app.main import allowed_hosts

    settings = Settings(environment="development", allowed_hosts=["plan.exemple.fr"])
    app = TrustedHostMiddleware(
        scope_reporter, allowed_hosts=allowed_hosts(settings), www_redirect=False
    )

    refused = await call(app, {"headers": [(b"host", b"evil.example")]})
    accepted = await call(app, {"headers": [(b"host", b"plan.exemple.fr")]})

    assert refused["status"] == 400
    assert accepted["status"] == 200


async def test_the_loopback_stays_allowed_when_hosts_are_restricted() -> None:
    """La sonde de santé du conteneur interroge la boucle locale : la fermer casserait le
    déploiement sans rien protéger — une redirection vers `localhost` ne mène nulle part."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from app.main import allowed_hosts

    settings = Settings(environment="development", allowed_hosts=["plan.exemple.fr"])
    app = TrustedHostMiddleware(
        scope_reporter, allowed_hosts=allowed_hosts(settings), www_redirect=False
    )

    assert (await call(app, {"headers": [(b"host", b"127.0.0.1:8000")]}))["status"] == 200


# --- Compression ------------------------------------------------------------------------------


def typed_app(content_type: str, size: int) -> Any:
    async def raw(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", content_type.encode())],
            }
        )
        await send({"type": "http.response.body", "body": b"a" * size})

    return SelectiveGZipMiddleware(raw)


def header_of(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode()
    return None


@pytest.mark.parametrize(
    ("content_type", "compressed"),
    [
        ("application/json", True),
        ("text/html; charset=utf-8", True),
        # Les plus grosses réponses du service. Les recompresser coûte du processeur dans la
        # boucle d'évènements pour un gain nul : ces formats sont déjà compressés.
        ("application/pdf", False),
        ("application/zip", False),
        ("image/png", False),
        ("application/octet-stream", False),
    ],
)
async def test_only_whitelisted_content_types_are_compressed(
    content_type: str, compressed: bool
) -> None:
    response = await call(
        typed_app(content_type, 4000), {"headers": [(b"accept-encoding", b"gzip")]}
    )

    assert (header_of(response["headers"], b"content-encoding") == "gzip") is compressed
    if compressed:
        assert len(response["body"]) < 4000
        assert "Accept-Encoding" in (header_of(response["headers"], b"vary") or "")
    else:
        assert response["body"] == b"a" * 4000


async def test_nothing_is_compressed_without_the_client_asking() -> None:
    response = await call(typed_app("application/json", 4000), {"headers": []})

    assert header_of(response["headers"], b"content-encoding") is None


async def test_a_small_response_is_left_alone() -> None:
    """En dessous du seuil, l'en-tête gzip pèse plus que ce qu'il fait gagner."""
    response = await call(
        typed_app("application/json", 20), {"headers": [(b"accept-encoding", b"gzip")]}
    )

    assert header_of(response["headers"], b"content-encoding") is None


# --- Ordre de la pile, vérifié sur l'application réelle ---------------------------------------


async def test_a_compressed_response_still_carries_the_security_headers(
    client: AsyncClient,
) -> None:
    """La compression est posée **sous** les en-têtes de sécurité.

    Dans l'autre sens, elle réécrirait `Content-Length` après coup et les en-têtes poseraient sur
    une réponse dont le corps a déjà changé de taille. C'est l'ordre, pas la présence de chaque
    middleware pris isolément, qui est vérifié ici.
    """
    response = await client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    # httpx décompresse : la longueur annoncée doit correspondre au corps compressé, pas au clair.
    assert int(response.headers["content-length"]) < len(response.content)


async def test_every_response_carries_a_request_identifier(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers.get("X-Request-Id")


async def test_the_error_response_carries_a_request_identifier_too(
    client: AsyncClient,
) -> None:
    """C'est sur une erreur que l'identifiant sert : c'est ce que l'utilisateur transmet."""
    response = await client.get("/api/projects")

    assert response.status_code == 401
    assert response.headers.get("X-Request-Id")


# --- Limitation de débit ----------------------------------------------------------------------


def test_the_three_login_buckets_are_keyed_differently() -> None:
    """Le défaut corrigé : le seau dit « par cible » était en fait clé sur `(IP, cible)`.

    500 adresses résidentielles donnaient donc 500 x 10 essais sur un même compte en cinq
    minutes. Le troisième seau, clé sur la seule adresse e-mail, est celui qui ferme ce trou.
    """
    from app.core.rate_limit import login_buckets

    keys_from_one_ip = {key for key, _ in login_buckets("1.1.1.1", "victime@exemple.fr")}
    keys_from_another = {key for key, _ in login_buckets("2.2.2.2", "victime@exemple.fr")}

    shared = keys_from_one_ip & keys_from_another
    assert shared == {"rl:login:account:victime@exemple.fr"}, (
        "aucun seau n'est commun aux deux adresses : une attaque distribuée passe entièrement"
    )


def test_the_account_bucket_blocks_a_distributed_attack() -> None:
    """Le scénario réel : une adresse IP différente à chaque tentative, une seule cible."""
    limiter = build_login_rate_limiter()
    quota = Settings(environment="development").login_attempts_per_account

    refused = 0
    for index in range(quota + 5):
        if not limiter.allow(f"192.0.2.{index}", "victime@exemple.fr", now=float(index)):
            refused += 1

    assert refused == 5, f"seules {quota + 5 - refused} tentatives ont été comptées"


def test_a_successful_login_does_not_open_the_client_bucket() -> None:
    limiter = build_login_rate_limiter()
    limiter.allow("1.1.1.1", "moi@exemple.fr", now=0.0)
    limiter.allow("1.1.1.1", "victime@exemple.fr", now=1.0)

    limiter.reset_target("1.1.1.1", "moi@exemple.fr")

    assert limiter.per_client.tracked_keys == 1, "le seau par adresse a été vidé par un succès"


def test_the_memory_fallback_is_stricter_than_the_shared_counter() -> None:
    """Exigence explicite : le repli ne doit jamais être plus permissif.

    Chaque worker ne voit qu'une fraction du trafic. Lui laisser le quota complet multiplierait le
    plafond réel par le nombre de workers — exactement le défaut qu'on corrige.
    """
    quota = Quota(60, 300.0)

    assert quota.tightened().max_events < quota.max_events
    assert quota.tightened().window_seconds == quota.window_seconds
    # Jamais zéro : un quota resserré à néant fermerait le service au lieu de le protéger.
    assert Quota(1, 300.0).tightened().max_events == 1


class FakeRedis:
    """Redis minimal, mais dont le script Lua est réellement exercé côté sémantique.

    Il compte les allers-retours : c'est ce qui prouve qu'une décision coûte **un seul** appel,
    donc qu'elle est atomique. Un `ZCARD` suivi d'un `ZADD` laisserait passer une requête de plus
    par worker à chaque tour.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.windows: dict[str, list[int]] = {}

    def register_script(self, script: str) -> Any:
        assert "ZREMRANGEBYSCORE" in script and "ZADD" in script

        async def run(keys: list[str], args: list[Any]) -> list[int]:
            self.calls += 1
            if self.fail:
                from redis.exceptions import ConnectionError as RedisConnectionError

                raise RedisConnectionError("redis absent")
            now, window, quota = int(args[0]), int(args[1]), int(args[2])
            events = [event for event in self.windows.get(keys[0], []) if event > now - window]
            if len(events) >= quota:
                self.windows[keys[0]] = events
                return [0, events[0] + window - now]
            events.append(now)
            self.windows[keys[0]] = events
            return [1, 0]

        return run

    async def delete(self, key: str) -> int:
        return 1 if self.windows.pop(key, None) is not None else 0


@pytest.fixture
def redis_limiter(monkeypatch: pytest.MonkeyPatch) -> tuple[RateLimiter, FakeRedis]:
    fake = FakeRedis()
    limiter = RateLimiter()
    monkeypatch.setattr(limiter, "_client", lambda: fake)
    return limiter, fake


def test_the_memory_store_refuses_rather_than_growing_past_its_cap() -> None:
    """Un plafond qu'on dépasse « juste cette fois » n'est pas un plafond.

    Le repli mémoire est le seul chemin où le compteur vit dans le processus : sans borne dure, un
    trafic distribué le fait grossir jusqu'à la mort du worker.
    """
    limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=60, max_keys=10)

    for index in range(10):
        assert limiter.check(f"ip-{index}", now=0.0).allowed

    refused = limiter.check("ip-de-trop", now=1.0)

    assert refused.allowed is False
    assert limiter.tracked_keys == 10
    assert refused.retry_after > 0


async def test_a_decision_costs_exactly_one_round_trip(
    redis_limiter: tuple[RateLimiter, FakeRedis],
) -> None:
    limiter, fake = redis_limiter

    await limiter.check("rl:test:a", Quota(3, 60.0))

    assert fake.calls == 1, "la décision n'est pas atomique : lire puis écrire est une course"


async def test_the_shared_counter_refuses_past_the_quota_and_says_when_to_retry(
    redis_limiter: tuple[RateLimiter, FakeRedis],
) -> None:
    limiter, _ = redis_limiter
    quota = Quota(3, 60.0)

    decisions = [await limiter.check("rl:test:b", quota) for _ in range(5)]

    assert [decision.allowed for decision in decisions] == [True, True, True, False, False]
    assert decisions[-1].retry_after > 0, "un 429 sans délai laisse le client retenter à l'aveugle"
    assert decisions[-1].retry_after <= 60


async def test_an_unreachable_redis_falls_back_more_strictly(
    monkeypatch: pytest.MonkeyPatch, journal: pytest.LogCaptureFixture
) -> None:
    """Une panne du compteur partagé dégrade le confort, elle n'ouvre pas la porte."""
    fake = FakeRedis(fail=True)
    limiter = RateLimiter()
    monkeypatch.setattr(limiter, "_client", lambda: fake)
    quota = Quota(8, 60.0)

    allowed = 0
    with journal.at_level(logging.WARNING):
        for _ in range(8):
            if (await limiter.check("rl:test:c", quota)).allowed:
                allowed += 1

    assert allowed == quota.tightened().max_events == 2
    assert limiter.degraded is True
    assert "repli" in journal.text


async def test_the_reusable_dependency_answers_429_with_a_retry_after() -> None:
    """`RateLimited(scope, max, window)` est la dépendance que l'API branchera sur ses routes
    coûteuses : elle doit refuser proprement, pas planter."""
    from fastapi import Depends, FastAPI

    guard = RateLimited("essai", 2, 60.0)
    api = FastAPI()

    @api.get("/couteux", dependencies=[Depends(guard)])
    async def couteux() -> dict[str, str]:
        return {"ok": "oui"}

    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        statuses = [(await ac.get("/couteux")) for _ in range(3)]

    assert [response.status_code for response in statuses] == [200, 200, 429]
    assert int(statuses[-1].headers["Retry-After"]) >= 1


def test_a_decision_carries_its_retry_delay() -> None:
    limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=30)
    limiter.check("ip", now=0.0)
    limiter.check("ip", now=10.0)

    refused = limiter.check("ip", now=20.0)

    assert refused == Decision(False, 10)


# --- Journalisation ---------------------------------------------------------------------------


@pytest.fixture
def journal(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Réarme les loggers applicatifs avant d'observer ce qu'ils écrivent.

    `tests/test_migrations.py` fait tourner Alembic, dont l'`env.py` appelle
    `logging.config.fileConfig` : cette fonction pose `disabled = True` sur tout logger qu'elle ne
    connaît pas — y compris les nôtres. Selon l'ordre de collecte, les journaux disparaissaient
    donc au milieu de la suite, silencieusement. `configure_logging` remet l'indicateur à zéro,
    et c'est aussi ce qui protège la production : le service de migration et le serveur peuvent
    partager une image.
    """
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    configure_logging(get_settings())
    return caplog


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.42", "203.0.113.0/24"),
        ("10.1.2.3", "10.1.2.0/24"),
        ("2001:db8:1234:5678::1", "2001:db8:1234::/48"),
        (None, "inconnu"),
        ("pas-une-adresse", "invalide"),
    ],
)
def test_addresses_are_never_logged_in_full(raw: str | None, expected: str) -> None:
    """Ligne RGPD du projet : journaux anonymisés, donc pas d'IP brute en production."""
    assert anonymized_ip(raw) == expected


def test_a_log_line_is_valid_json_with_its_extra_fields() -> None:
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "message", None, None)
    record.__dict__["champ"] = "valeur"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "message"
    assert payload["champ"] == "valeur"


@pytest.fixture
async def logged_app() -> AsyncIterator[AsyncClient]:
    async def endpoint(request: Any) -> Response:
        return PlainTextResponse(current_request_id())

    inner = Starlette(routes=[Route("/trace", endpoint)])
    transport = ASGITransport(app=RequestContextMiddleware(inner))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_the_request_id_is_returned_and_visible_from_the_handler(
    logged_app: AsyncClient,
) -> None:
    """Un utilisateur qui signale un incident donne un identifiant qui retrouve la trace."""
    response = await logged_app.get("/trace")

    assert response.headers["X-Request-Id"]
    assert response.text == response.headers["X-Request-Id"]


async def test_an_inbound_request_id_is_reused_for_correlation(logged_app: AsyncClient) -> None:
    response = await logged_app.get("/trace", headers={"X-Request-Id": "abc-123"})

    assert response.headers["X-Request-Id"] == "abc-123"


async def test_a_forged_request_id_cannot_inject_into_the_logs(logged_app: AsyncClient) -> None:
    """Un identifiant est recopié dans un en-tête et dans le journal : il ne peut pas être libre."""
    response = await logged_app.get("/trace", headers={"X-Request-Id": "abc\r\nX-Injecte: 1"})

    assert response.headers["X-Request-Id"] != "abc\r\nX-Injecte: 1"
    assert "X-Injecte" not in response.headers


async def test_the_access_log_never_carries_the_query_string(
    logged_app: AsyncClient, journal: pytest.LogCaptureFixture
) -> None:
    """Une chaîne de requête peut porter un jeton de partage : dans un journal, il cesse d'être
    secret."""
    with journal.at_level(logging.INFO, logger="app.access"):
        await logged_app.get("/trace", params={"jeton": "secret-de-partage"})

    # Les enregistrements du journal d'accès seulement : httpx journalise l'URL complète de son
    # côté, ce qui n'a rien à voir avec ce que le service écrit.
    access = [record for record in journal.records if record.name == "app.access"]
    assert access
    assert all("secret-de-partage" not in str(record.__dict__) for record in access)
    assert access[-1].__dict__["path"] == "/trace"


def test_a_security_event_truncates_the_address(journal: pytest.LogCaptureFixture) -> None:
    from app.core.logging import log_security_event

    with journal.at_level(logging.WARNING, logger="app.security"):
        log_security_event("essai", client_host="203.0.113.42", detail="x")

    assert "203.0.113.42" not in journal.text
    assert journal.records[-1].__dict__["client"] == "203.0.113.0/24"


@pytest.mark.parametrize(
    ("status", "event"),
    [(401, "auth.refused"), (403, "access.denied"), (429, "rate_limit.exceeded")],
)
async def test_a_refusal_becomes_a_security_event(
    journal: pytest.LogCaptureFixture, status: int, event: str
) -> None:
    """Les refus sont reconnus au code de statut, pas déclarés route par route.

    Un appel posé dans chaque route se périme au premier endpoint ajouté par quelqu'un qui ne
    connaît pas la convention. C'est ce qui couvre l'échec d'authentification de l'API sans que
    `app/api/auth.py` ait à s'en occuper.
    """

    async def endpoint(request: Any) -> Response:
        return PlainTextResponse("non", status_code=status)

    inner = Starlette(routes=[Route("/refus", endpoint)])
    transport = ASGITransport(app=RequestContextMiddleware(inner))
    with journal.at_level(logging.WARNING, logger="app.security"):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get("/refus")

    events = [record.__dict__.get("event") for record in journal.records]
    assert event in events


async def test_a_successful_request_is_not_a_security_event(
    journal: pytest.LogCaptureFixture,
) -> None:
    """Sinon le journal de sécurité devient le journal d'accès, et ne sert plus à rien."""

    async def endpoint(request: Any) -> Response:
        return PlainTextResponse("oui")

    inner = Starlette(routes=[Route("/ok", endpoint)])
    transport = ASGITransport(app=RequestContextMiddleware(inner))
    with journal.at_level(logging.WARNING, logger="app.security"):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get("/ok")

    assert [record for record in journal.records if record.name == "app.security"] == []


# --- Sondes -----------------------------------------------------------------------------------


async def test_liveness_never_touches_a_dependency(client: AsyncClient) -> None:
    """Si elle interrogeait la base, une coupure momentanée ferait redémarrer en boucle des
    processus sains — et la panne durerait plus longtemps que sa cause."""
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_each_component(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_readiness_answers_503_and_names_the_faulty_component(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un 503 muet n'apprend rien à l'astreinte, qui doit alors deviner ce qui est tombé."""
    from app.core import probes

    async def broken() -> str:
        return "OperationalError"

    monkeypatch.setattr(probes, "_check_database", broken)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] == "OperationalError"


async def test_readiness_does_not_hang_on_a_frozen_dependency(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une sonde qui attend indéfiniment n'est plus une sonde : c'est une requête de plus qui
    s'accumule pendant que la panne dure."""
    from app.core import cache, probes

    class FrozenRedis:
        async def ping(self) -> bool:
            await asyncio.sleep(30)
            return True

    monkeypatch.setattr(probes, "PROBE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cache, "get_client", lambda: FrozenRedis())
    monkeypatch.setattr(
        probes, "get_settings", lambda: Settings(environment="development", cache_enabled=True)
    )

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "délai dépassé"


# --- Back-office ------------------------------------------------------------------------------


def test_the_admin_engine_is_created_once() -> None:
    """Il était construit **et détruit** à chaque requête : chaque page du back-office ouvrait
    une connexion PostgreSQL neuve, en payait la poignée de main, puis la jetait."""
    from app.admin import sync_engine

    assert sync_engine() is sync_engine()


def test_the_admin_no_longer_disposes_its_engine_on_every_call() -> None:
    import inspect

    from app import admin

    assert "engine.dispose()" not in inspect.getsource(admin)


async def test_the_admin_session_cookie_is_short_lived_and_strict(client: AsyncClient) -> None:
    """Un CRUD complet sur toutes les données n'a aucune raison de rester ouvert 14 jours, et
    `SameSite=Lax` laisse le cookie partir sur une navigation initiée par un site tiers."""
    from app.admin import AdminAuth

    backend = AdminAuth(
        secret_key="k" * 40,
        session_cookie="admin_session",
        max_age=3600,
        path="/admin",
        same_site="strict",
        https_only=False,
    )
    options = backend.middlewares[0].kwargs

    assert options["max_age"] == 3600
    assert options["same_site"] == "strict"
    assert options["path"] == "/admin"


async def test_the_admin_verifies_passwords_outside_the_event_loop() -> None:
    """Argon2id coûte ~35 ms mesurées. Dans la boucle, ces 35 ms bloquent toutes les autres
    requêtes du worker : c'est un déni de service déclenché par un simple formulaire."""
    import inspect

    from app.admin import AdminAuth

    source = inspect.getsource(AdminAuth.login)
    assert "run_in_threadpool" in source
    assert "verify_password" in source


# --- Ligne de commande ------------------------------------------------------------------------


async def test_creating_a_superuser_stores_a_usable_account(
    engine: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sans cette commande, la seule façon de créer le premier administrateur était un UPDATE à
    la main en base de production."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel import col, select

    from app import cli
    from app.core.security import verify_password
    from app.models.user import User

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)

    assert await cli._create_superuser("Patron@Exemple.FR", "mot-de-passe-solide-2026") == 0

    async with factory() as session:
        created = (
            await session.execute(select(User).where(col(User.email) == "patron@exemple.fr"))
        ).scalar_one()

    # Adresse normalisée comme à l'inscription, sinon `Patron@…` et `patron@…` sont deux comptes.
    assert created.is_superuser and created.is_active
    assert verify_password("mot-de-passe-solide-2026", created.hashed_password)
    assert "mot-de-passe-solide" not in capsys.readouterr().out


async def test_promoting_an_existing_account_is_allowed(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cas réel est « ce compte existe déjà et doit devenir administrateur »."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel import col, select

    from app import cli
    from app.models.user import User

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)
    async with factory() as session:
        session.add(User(email="deja@exemple.fr", hashed_password="argon2-factice"))
        await session.commit()

    assert await cli._create_superuser("deja@exemple.fr", "mot-de-passe-solide-2026") == 0

    async with factory() as session:
        promoted = (
            await session.execute(select(User).where(col(User.email) == "deja@exemple.fr"))
        ).scalar_one()
    assert promoted.is_superuser


def test_the_password_is_read_from_standard_input_not_from_the_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La ligne de commande est lisible par tout le monde dans `ps`, et finit dans l'historique
    du shell : un mot de passe n'y a pas sa place."""
    import contextlib
    import io
    import sys

    from app import cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("depuis-l-entree-standard\n"))
    assert cli.read_password() == "depuis-l-entree-standard"

    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text), contextlib.suppress(SystemExit):
        cli.main(["create-superuser", "--help"])
    assert "--password" not in help_text.getvalue()


def test_a_weak_superuser_password_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le refus intervient avant toute écriture : un compte à tous les droits n'a pas vocation à
    être le seul à pouvoir être faible."""
    from app import cli

    monkeypatch.setattr(cli, "read_password", lambda *args: "court")

    assert cli.main(["create-superuser", "patron@exemple.fr"]) == 2
    assert "trop court" in capsys.readouterr().err
