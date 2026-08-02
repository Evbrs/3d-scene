"""Health check — seule route exposée par le scaffolding P0."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Sonde de vivacité utilisée par Docker, la CI et les critères d'acceptation P0."""
    return HealthResponse(status="ok")
