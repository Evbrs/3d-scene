"""Critère d'acceptation P0 : GET /health retourne 200 {"status": "ok"}."""

from httpx import AsyncClient


async def test_health_returns_200_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_schema_is_served(client: AsyncClient) -> None:
    """Le schéma OpenAPI est la source de vérité du frontend (plan §6) : il doit être servi."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
