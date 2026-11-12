"""Version one routes."""

from fastapi import APIRouter

from backend.api.v1 import config, forecasts, health, legacy_capacity, llm, scenarios, status, synthetic
from backend.api.v1.comparisons import router as comparisons_router
from backend.api.v1.decision_assistant import router as decision_assistant_router
from backend.api.v1.decision_evidence import router as evidence_router
from backend.api.v1.decision_exports import exports_router
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
router.include_router(synthetic.router)
router.include_router(comparisons_router)
router.include_router(evidence_router)
router.include_router(decision_assistant_router)
router.include_router(exports_router)
