"""Preserved capacity sensitivity endpoint used by the current frontend."""

from fastapi import APIRouter

from backend.scenarios import capacity_balance
from backend.schemas.capacity import CapacityInput, CapacityResponse

router = APIRouter(tags=["Existing scenario sensitivity"])


@router.post(
    "/scenarios/capacity", response_model=CapacityResponse, summary="Рассчитать чувствительность к введённой мощности"
)
def calculate_capacity(inputs: CapacityInput) -> CapacityResponse:
    balance = capacity_balance(
        demand_tonnes=inputs.demand_tonnes,
        capacity_tonnes=inputs.capacity_tonnes,
        capacity_delta_tonnes=inputs.capacity_delta_tonnes,
    )
    return CapacityResponse(
        period=inputs.period,
        assumption_source=inputs.assumption_source,
        **balance,
        warning="Расчёт по введённым допущениям; не прогноз и не подтверждённый эффект проекта.",
    )
