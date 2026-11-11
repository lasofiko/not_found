"""Version one routes."""

from fastapi import APIRouter

from backend.api.v1 import config, forecasts, health, legacy_capacity, llm, scenarios, status
from backend.schemas.common import ErrorResponse

router = APIRouter(
    responses={
        404: {"model": ErrorResponse, "description": "Маршрут не найден"},
        405: {"model": ErrorResponse, "description": "Метод не поддерживается"},
        422: {"model": ErrorResponse, "description": "Некорректные параметры запроса"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
    }
)
router.include_router(health.router)
router.include_router(config.router)
router.include_router(status.router)
router.include_router(legacy_capacity.router)
router.include_router(forecasts.router)
router.include_router(scenarios.router)
router.include_router(llm.router)
