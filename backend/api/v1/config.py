"""Approved project options and currently available analytical options."""

from fastapi import APIRouter, Request

from backend.api.v1.status import get_status
from backend.schemas.config import (
    AvailableOptions,
    ConfigResponse,
    Direction,
    Granularity,
    Horizon,
    MassUnit,
    SupportedOptions,
    TransportMode,
)

router = APIRouter(tags=["Infrastructure"])


@router.get("/config", response_model=ConfigResponse, summary="Получить конфигурацию проекта")
def get_config(request: Request) -> ConfigResponse:
    connected = get_status(request).capabilities.forecast
    return ConfigResponse(
        supported=SupportedOptions(
            directions=list(Direction),
            transport_modes=list(TransportMode),
            mass_units=list(MassUnit),
            granularities=list(Granularity),
            horizon=Horizon(),
        ),
        available=AvailableOptions(
            history_granularities=[],
            forecast_granularities=[Granularity.year] if connected else [],
            forecast_target_years=list(range(2024, 2031)) if connected else [],
        ),
    )
