"""Existing user-input-only capacity sensitivity contract."""

import math
import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class CapacityInput(BaseModel):
    demand_tonnes: float = Field(ge=0)
    capacity_tonnes: float = Field(ge=0)
    capacity_delta_tonnes: float = Field(ge=0)
    period: str
    assumption_source: str = Field(min_length=1)

    @field_validator("demand_tonnes", "capacity_tonnes", "capacity_delta_tonnes", mode="before")
    @classmethod
    def finite_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Логическое значение не является массой")
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        except OverflowError as exc:
            raise ValueError("Значение выходит за диапазон конечных чисел float64") from exc
        if not math.isfinite(number):
            raise ValueError("Значение должно быть конечным числом в диапазоне float64")
        return value

    @field_validator("assumption_source")
    @classmethod
    def valid_source(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 2000:
            raise ValueError("Укажите источник длиной от 1 до 2000 символов")
        return value

    @field_validator("period")
    @classmethod
    def calendar_period(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2})?", value):
            raise ValueError("Период должен иметь формат YYYY или YYYY-MM")
        try:
            date.fromisoformat(value + ("-01" if len(value) == 7 else "-01-01"))
        except ValueError as exc:
            raise ValueError("Период должен содержать год 0001–9999 и месяц 01–12") from exc
        return value

    @model_validator(mode="after")
    def finite_capacity_sum(self) -> Self:
        if not math.isfinite(self.capacity_tonnes + self.capacity_delta_tonnes):
            raise ValueError("Сумма доступной и дополнительной мощности выходит за диапазон float64")
        return self


class CapacityResponse(BaseModel):
    period: str
    unit: Literal["tonnes"] = "tonnes"
    status: Literal["scenario"] = "scenario"
    assumption_source: str
    baseline_shortfall_tonnes: float
    scenario_capacity_tonnes: float
    scenario_shortfall_tonnes: float
    shortfall_reduction_tonnes: float
    scenario_spare_capacity_tonnes: float
    warning: str
