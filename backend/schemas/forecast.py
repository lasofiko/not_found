"""Годовой прогноз существующей модели линии и явная недоступность."""

from typing import Any, Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator


class Bounds(BaseModel):
    lower: FiniteFloat = Field(ge=0)
    upper: FiniteFloat = Field(ge=0)


class ForecastPoint(BaseModel):
    period: str = Field(pattern=r"^20[0-9]{2}$")
    period_granularity: Literal["year"]
    horizon_years: int = Field(ge=1, le=7)
    value: FiniteFloat = Field(ge=0)
    interval: dict[Literal["80", "95"], Bounds]

    @model_validator(mode="after")
    def ordered_intervals(self) -> "ForecastPoint":
        if set(self.interval) != {"80", "95"}:
            raise ValueError("Отсутствуют интервалы прогноза")
        lo, hi = self.interval["95"], self.interval["80"]
        if not lo.lower <= hi.lower <= self.value <= hi.upper <= lo.upper:
            raise ValueError("Нарушен порядок интервалов")
        return self


class ForecastResponse(BaseModel):
    status: Literal["ready"] = "ready"
    result_type: Literal["forecast"] = "forecast"
    target_id: Literal["ust_luga_kaliningrad_ferry"] = "ust_luga_kaliningrad_ferry"
    target_name: str = "Годовая перевезённая масса линии Усть-Луга — Балтийск"
    region: Literal["kaliningrad"] = "kaliningrad"
    direction: Literal["inbound", "outbound"]
    mode: Literal["sea"]
    unit: Literal["tonnes"]
    geography: dict[str, str]
    training_period: dict[str, str]
    forecast_origin: int
    observations: int
    selected_model: str
    data_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    generated_at: str
    predictions: list[ForecastPoint] = Field(min_length=1)
    provenance: dict[str, Any]
    metrics: dict[str, Any]
    warnings: list[str]


class ForecastProblem(BaseModel):
    status: Literal["not_connected", "not_available", "error"]
    message: str
