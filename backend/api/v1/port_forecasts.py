"""HTTP-доступ к месячным прогнозам портового каботажа, выбранным для сайта."""

import logging
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from backend.port_forecast_service import PortModelNotConnected, PortTargetNotFound

router = APIRouter(tags=["Port forecasts"])
logger = logging.getLogger(__name__)
PERIOD = re.compile(r"^20(2[6-9]|30)-(0[1-9]|1[0-2])$")


def _problem(status_code: int, status: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": status, "message": message})


@router.get("/port-forecasts")
def get_port_catalog(request: Request):
    try:
        return request.app.state.port_forecast_service.catalog()
    except (ValueError, OSError, KeyError, TypeError):
        logger.exception("Port catalog validation failed")
        return _problem(500, "error", "Не удалось проверить реестр сайта и итоговые метрики (docs/final).")


@router.get("/port-forecasts/{target_id}")
def get_port_forecast(request: Request, target_id: str, end_period: str = Query(default="2026-12")):
    if not PERIOD.match(end_period):
        return _problem(422, "invalid_request", "end_period: месяц от 2026-01 до 2030-12 в формате YYYY-MM.")
    try:
        return request.app.state.port_forecast_service.get(target_id, end_period)
    except PortTargetNotFound:
        return _problem(404, "not_available", "Цель не опубликована на сайте: " + target_id)
    except PortModelNotConnected as error:
        return _problem(503, "not_connected", str(error))
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        logger.exception("Port forecast validation failed")
        return _problem(500, "error", "Не удалось проверить модель порта. Проверьте models/port и SHA-256.")
