"""Ticket P12 — durcissement du déploiement.

Ces tests portent sur des propriétés de sécurité qui, faute d'assertion, se perdent
silencieusement à la première refonte de configuration.
"""

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import DEV_SECRET_KEY, Settings
from app.core.security_headers import API_CSP

# Une configuration de production minimale et valide : la liste d'origines est vide, ce qui est
# exactement ce que déploie `docker-compose.prod.yml` quand le frontend et l'API partagent la même
# origine.
PRODUCTION: dict[str, Any] = {
    "environment": "production",
    "secret_key": "k" * 48,
    "cors_origins": [],
}

# --- Configuration : le démarrage échoue plutôt que de démarrer mal ------------------------


def test_production_refuses_the_development_key() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", secret_key=DEV_SECRET_KEY)


def test_production_refuses_a_short_key() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", secret_key="trop-court")


def test_forgetting_the_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le défaut est « production » : un oubli doit empêcher le démarrage, pas le permettre.

    La suite de tests positionne `ENVIRONMENT=development` : il faut donc le retirer pour
    observer le vrai défaut, sans quoi ce test validerait la configuration des tests.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert Settings(secret_key="x" * 40, cors_origins=[]).environment == "production"
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(secret_key=DEV_SECRET_KEY)


def test_a_strong_key_is_accepted_in_production() -> None:
    settings = Settings(**PRODUCTION)
    assert settings.is_development is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.fr,https://b.fr", ["https://a.fr", "https://b.fr"]),
        ("https://a.fr, https://b.fr ", ["https://a.fr", "https://b.fr"]),
        ('["https://c.fr"]', ["https://c.fr"]),
        ("https://seul.fr", ["https://seul.fr"]),
    ],
)
def test_cors_origins_accept_both_notations(raw: str, expected: list[str]) -> None:
    """`CORS_ORIGINS=https://exemple.fr` est la forme qu'on écrit spontanément.

    `pydantic-settings` n'accepte nativement que du JSON pour une liste : sans ce validateur, la
    forme naturelle ferait échouer le démarrage en production.
    """
    assert Settings(cors_origins=raw).cors_origins == expected


@pytest.mark.parametrize("origin", ["*", "null"])
def test_production_refuses_a_wildcard_origin(origin: str) -> None:
    """Le piège : `allow_credentials=True` **plus** un joker.

    Starlette ne renvoie alors pas `*` mais **reflète l'origine du demandeur**, et le navigateur
    accepte. La protection paraît en place et n'existe plus : n'importe quel site tiers lit les
    réponses authentifiées de l'utilisateur connecté. Le refus doit donc être au démarrage.
    """
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(**{**PRODUCTION, "cors_origins": [origin]})


def test_production_demands_https_origins() -> None:
    with pytest.raises(ValueError, match="https://"):
        Settings(**{**PRODUCTION, "cors_origins": ["http://plan.exemple.fr"]})


def test_development_keeps_its_local_origin() -> None:
    """Le durcissement ne doit pas rendre le développement impraticable : Vite est en clair."""
    settings = Settings(environment="development", cors_origins=["http://localhost:5173"])
    assert settings.cors_origins == ["http://localhost:5173"]


def test_production_refuses_to_trust_every_proxy() -> None:
    """`TRUSTED_PROXIES=*` rendrait `X-Forwarded-For` de nouveau choisissable par le visiteur."""
    with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
        Settings(**{**PRODUCTION, "trusted_proxies": ["*"]})


def test_the_admin_session_key_is_not_the_jwt_key() -> None:
    """Une fuite du cookie d'administration ne doit pas livrer de quoi forger un jeton d'API."""
    settings = Settings(**PRODUCTION)

    assert settings.admin_session_key != settings.secret_key
    assert len(settings.admin_session_key) >= 32
    # Dérivée, donc stable : deux démarrages successifs ne doivent pas invalider les sessions.
    assert settings.admin_session_key == Settings(**PRODUCTION).admin_session_key
    # Et une clé fournie explicitement l'emporte.
    explicit = Settings(**{**PRODUCTION, "admin_session_secret_key": "a" * 40})
    assert explicit.admin_session_key == "a" * 40


def test_the_cache_and_the_broker_do_not_share_a_redis_database() -> None:
    """Sinon une session d'édition chargée évince les messages de la file d'export."""
    settings = Settings(environment="development", redis_url="redis://exemple:6379/0")

    urls = {settings.broker_redis_url, settings.cache_redis_url, settings.rate_limit_redis_url}
    assert len(urls) == 3, urls
    assert settings.broker_redis_url == "redis://exemple:6379/0"


# --- En-têtes de sécurité --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
    ],
)
async def test_security_headers_are_present(
    client: AsyncClient, header: str, expected: str
) -> None:
    response = await client.get("/health")
    assert response.headers.get(header) == expected


async def test_the_api_declares_a_strict_content_security_policy(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers.get("Content-Security-Policy") == API_CSP
    assert "default-src 'none'" in API_CSP


async def test_the_admin_gets_its_own_policy(client: AsyncClient) -> None:
    """La CSP de l'API casserait le back-office, qui sert ses propres feuilles et scripts."""
    response = await client.get("/admin/login")
    policy = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


async def test_hsts_is_absent_in_development(client: AsyncClient) -> None:
    """En local le service est en clair : HSTS y bloquerait le navigateur pour un an."""
    response = await client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


async def test_authenticated_responses_are_not_cacheable(auth_client: AsyncClient) -> None:
    """Un intermédiaire partagé ne doit pas conserver une réponse authentifiée."""
    response = await auth_client.get("/api/projects")
    assert response.headers.get("Cache-Control") == "no-store"


async def test_the_public_share_endpoint_stays_usable(client: AsyncClient) -> None:
    """Le durcissement ne doit pas casser l'endpoint public : il n'a pas de jeton, donc pas de
    `no-store` imposé."""
    response = await client.get("/api/public/views/" + "z" * 43)
    assert response.status_code == 404
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


# --- Régressions issues de la revue finale ---------------------------------------------------


async def test_security_headers_are_present_on_a_preflight(client: AsyncClient) -> None:
    """Régression : le middleware CORS répondait seul aux préflights, hors de la pile.

    Les en-têtes de sécurité étaient donc absents de ces réponses. Le middleware doit être le
    plus externe pour les couvrir.
    """
    response = await client.request(
        "OPTIONS",
        "/api/projects",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


async def test_security_headers_survive_an_unhandled_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Régression : la réponse 500 par défaut sort hors de la pile, donc sans en-têtes.

    C'est pourtant le moment où ils comptent le plus — et le corps ne doit rien révéler.

    Ce test était **toujours** ignoré : le client des fixtures est construit sans
    `raise_app_exceptions=False`, donc l'exception remontait dans le test et le `pytest.skip`
    s'exécutait systématiquement. Il ne protégeait donc rien du tout. Le transport est ici
    construit sur place avec l'option qui manquait, et l'assertion s'exécute vraiment.
    """
    from app.main import app

    @app.get("/_test/boom", include_in_schema=False)
    async def _boom() -> None:  # pragma: no cover - route de test
        raise RuntimeError("détail interne qui ne doit pas fuiter")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/_test/boom")

    assert response.status_code == 500
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert "détail interne" not in response.text

    # Muet pour le client, bavard dans le journal : sans cela l'incident serait perdu.
    logged = [record for record in caplog.records if record.exc_info]
    assert logged, "la trace de l'exception n'a pas été journalisée"
    assert "détail interne qui ne doit pas fuiter" in caplog.text


def test_the_rate_limiter_does_not_leak_memory() -> None:
    """Régression : le dictionnaire du limiteur gardait une entrée par IP, indéfiniment.

    Sur l'unique endpoint public du projet, c'est un levier de saturation mémoire offert à qui
    n'a même pas de compte.

    L'assertion précédente (`tracked_keys <= 500` après 500 clés insérées) était vraie quoi qu'il
    arrive : elle ne pouvait pas échouer, y compris avec une fuite complète. Le vrai défaut est
    ailleurs — une clé vue une fois puis jamais revue n'était **jamais** reprise, parce que la
    purge n'a lieu qu'en revisitant la clé. C'est ce cas-là qui est mesuré ici.
    """
    from app.core.rate_limit import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=10, max_keys=500)

    for index in range(500):
        limiter.hit(f"ip-{index}", now=0.0)
    assert limiter.tracked_keys == 500

    # Une seule tentative, longtemps après, sur une adresse jamais vue : les 500 fenêtres
    # écoulées doivent être rendues. Sans balayage, le compteur monterait à 501.
    limiter.hit("ip-tardive", now=1000.0)

    assert limiter.tracked_keys == 1, "les clés expirées ne sont jamais reprises"
