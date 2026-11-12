"""Read-only coverage and compatible inbound/outbound comparison."""

from fastapi import APIRouter, Query, Request

from backend.core.exceptions import error_response
from backend.decision_evidence import build_coverage, build_direction_profile

router = APIRouter(tags=["Decision evidence"])


@router.get("/direction-profile", summary="Сравнить ввоз и вывоз по одному артефакту")
def direction_profile(request: Request, end_year: int = Query(2030, ge=2024, le=2030),
                      region: str = "kaliningrad", mode: str = "sea"):
    if region != "kaliningrad" or mode != "sea":
        return error_response(404, "PROFILE_NOT_AVAILABLE",
                              "Профиль подключён только для морской линии Усть-Луга — Балтийск.")
    try:
        return build_direction_profile(request.app.state.forecast_service, end_year)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        return error_response(409, "PROFILE_CONTEXT_INVALID",
                              "Не удалось подтвердить совместимые версии, историю и происхождение направлений.")


@router.get("/coverage", summary="Показать фактические стадии доступности данных")
def coverage(request: Request):
    return build_coverage(request.app.state.forecast_service)
