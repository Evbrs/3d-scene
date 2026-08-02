"""Ticket P12 — durcissement du déploiement.

Ces tests portent sur des propriétés de sécurité qui, faute d'assertion, se perdent
silencieusement à la première refonte de configuration.
"""

import pytest
from httpx import AsyncClient

from app.core.config import DEV_SECRET_KEY, Settings
from app.core.security_headers import API_CSP

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

    assert Settings(secret_key="x" * 40).environment == "production"
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(secret_key=DEV_SECRET_KEY)


def test_a_strong_key_is_accepted_in_production() -> None:
    settings = Settings(environment="production", secret_key="k" * 48)
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
