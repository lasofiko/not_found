"""Минимальный API и страница для проверки сценарного контракта."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.scenarios import capacity_balance

app = FastAPI(title="Балтийский баланс — 2030", version="0.1.0")
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class CapacityInput(BaseModel):
    demand_tonnes: float = Field(ge=0, allow_inf_nan=False)
    capacity_tonnes: float = Field(ge=0, allow_inf_nan=False)
    capacity_delta_tonnes: float = Field(ge=0, allow_inf_nan=False)
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
    try:
        balance = capacity_balance(demand_tonnes=inputs.demand_tonnes, capacity_tonnes=inputs.capacity_tonnes,
                                   capacity_delta_tonnes=inputs.capacity_delta_tonnes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "period": inputs.period,
        "unit": "tonnes",
        "status": "scenario",
        "assumption_source": inputs.assumption_source,
        **balance,
        "warning": "Расчёт по введённым допущениям; не прогноз и не подтверждённый эффект проекта.",
    }
