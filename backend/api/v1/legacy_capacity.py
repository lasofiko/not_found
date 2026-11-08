"""Preserved capacity sensitivity endpoint used by the current frontend."""

from fastapi import APIRouter

from backend.schemas.capacity import CapacityInput, CapacityResponse

router = APIRouter(tags=["Existing scenario sensitivity"])


@router.post(
    "/scenarios/capacity", response_model=CapacityResponse, summary="Рассчитать чувствительность к введённой мощности"
)
def calculate_capacity(inputs: CapacityInput) -> CapacityResponse:
    baseline_shortfall = max(0.0, inputs.demand_tonnes - inputs.capacity_tonnes)
    scenario_capacity = inputs.capacity_tonnes + inputs.capacity_delta_tonnes
    scenario_shortfall = max(0.0, inputs.demand_tonnes - scenario_capacity)
    return CapacityResponse(
        period=inputs.period,
        assumption_source=inputs.assumption_source,
        baseline_shortfall_tonnes=baseline_shortfall,
        scenario_capacity_tonnes=scenario_capacity,
        scenario_shortfall_tonnes=scenario_shortfall,
        shortfall_reduction_tonnes=baseline_shortfall - scenario_shortfall,
        warning="Расчёт по введённым допущениям; не прогноз и не подтверждённый эффект проекта.",
    )
