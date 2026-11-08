"""Approved project options and currently available analytical options."""

from fastapi import APIRouter

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
def get_config() -> ConfigResponse:
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
            forecast_granularities=[],
            forecast_target_years=[],
        ),
    )
