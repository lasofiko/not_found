"""HTTP-доступ к проверенному артефакту годовой линии."""

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from backend.schemas.forecast import ForecastProblem, ForecastResponse

router = APIRouter(tags=["Forecasts"])
logger = logging.getLogger(__name__)


@router.get(
    "/forecasts",
    response_model=ForecastResponse,
    responses={
        404: {"model": ForecastProblem},
        503: {"model": ForecastProblem},
        500: {"model": ForecastProblem},
    },
)
def get_forecasts(
    request: Request,
    region: str = "kaliningrad",
    direction: str = "inbound",
    mode: str = "sea",
    end_year: int = Query(default=2030, ge=2024, le=2030),
):
    if region != "kaliningrad" or mode != "sea" or direction not in {"inbound", "outbound"}:
        problem = ForecastProblem(
            status="not_available", message="Поддержана только морская линия Усть-Луга — Балтийск, inbound/outbound."
        )
        return JSONResponse(status_code=404, content=problem.model_dump())
    try:
        return request.app.state.forecast_service.get(direction, end_year)
    except FileNotFoundError as error:
        problem = ForecastProblem(status="not_connected", message=str(error))
        return JSONResponse(status_code=503, content=problem.model_dump())
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        logger.exception("Forecast artifact validation failed")
        problem = ForecastProblem(
            status="error", message="Не удалось проверить модель. Проверьте комплект и SHA-256 артефактов."
        )
        return JSONResponse(status_code=500, content=problem.model_dump())
