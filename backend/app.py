"""Минимальный API и страница для проверки сценарного контракта."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Балтийский баланс — 2030", version="0.1.0")
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class CapacityInput(BaseModel):
    demand_tonnes: float = Field(ge=0)
    capacity_tonnes: float = Field(ge=0)
    capacity_delta_tonnes: float = Field(ge=0)
    period: str = Field(pattern=r"^\d{4}(-\d{2})?$")
    assumption_source: str = Field(min_length=1)


@app.get("/")
def home() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/frontend/app.js")
def frontend_script() -> FileResponse:
    return FileResponse(FRONTEND / "app.js", media_type="application/javascript")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/v1/scenarios/capacity")
def calculate_capacity(inputs: CapacityInput) -> dict:
    baseline_shortfall = max(0.0, inputs.demand_tonnes - inputs.capacity_tonnes)
    scenario_capacity = inputs.capacity_tonnes + inputs.capacity_delta_tonnes
    scenario_shortfall = max(0.0, inputs.demand_tonnes - scenario_capacity)
    return {
        "period": inputs.period,
        "unit": "tonnes",
        "status": "scenario",
        "assumption_source": inputs.assumption_source,
        "baseline_shortfall_tonnes": baseline_shortfall,
        "scenario_capacity_tonnes": scenario_capacity,
        "scenario_shortfall_tonnes": scenario_shortfall,
        "shortfall_reduction_tonnes": baseline_shortfall - scenario_shortfall,
        "warning": "Расчёт по введённым допущениям; не прогноз и не подтверждённый эффект проекта.",
    }
