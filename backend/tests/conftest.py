"""Fixtures partagées.

Le client est asynchrone (`httpx.AsyncClient` + `ASGITransport`), conformément à la stack de
test annoncée dans docs/spec-complete.md §7 (P11).
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
