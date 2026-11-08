"""Server liveness endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Infrastructure"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["baltic-balance-backend"] = "baltic-balance-backend"
    api_version: Literal["v1"] = "v1"


@router.get("/health", response_model=HealthResponse, summary="Проверить работу сервера")
def health() -> HealthResponse:
    return HealthResponse()
