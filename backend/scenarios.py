"""Баланс заданной нагрузки и провозной возможности за один период."""

import math


def capacity_balance(*, demand_tonnes: float, capacity_tonnes: float, capacity_delta_tonnes: float) -> dict[str, float]:
    inputs = (demand_tonnes, capacity_tonnes, capacity_delta_tonnes)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in inputs):
        raise ValueError("Нагрузка и провозная возможность должны быть конечными неотрицательными числами.")
    scenario_capacity = capacity_tonnes + capacity_delta_tonnes
    if not math.isfinite(scenario_capacity):
        raise ValueError("Суммарная провозная возможность слишком велика.")
    baseline_shortfall = max(0.0, demand_tonnes - capacity_tonnes)
    scenario_shortfall = max(0.0, demand_tonnes - scenario_capacity)
    return {
        "baseline_shortfall_tonnes": baseline_shortfall,
        "scenario_capacity_tonnes": scenario_capacity,
        "scenario_shortfall_tonnes": scenario_shortfall,
        "shortfall_reduction_tonnes": baseline_shortfall - scenario_shortfall,
        "scenario_spare_capacity_tonnes": max(0.0, scenario_capacity - demand_tonnes),
    }
