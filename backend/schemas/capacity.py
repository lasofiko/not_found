"""Existing user-input-only capacity sensitivity contract."""

from typing import Literal

from pydantic import BaseModel, Field


class CapacityInput(BaseModel):
    demand_tonnes: float = Field(ge=0)
    capacity_tonnes: float = Field(ge=0)
    capacity_delta_tonnes: float = Field(ge=0)
    period: str = Field(pattern=r"^\d{4}(-\d{2})?$")
    assumption_source: str = Field(min_length=1)


class CapacityResponse(BaseModel):
    period: str
    unit: Literal["tonnes"] = "tonnes"
    status: Literal["scenario"] = "scenario"
    assumption_source: str
    baseline_shortfall_tonnes: float
    scenario_capacity_tonnes: float
    scenario_shortfall_tonnes: float
    shortfall_reduction_tonnes: float
    warning: str
