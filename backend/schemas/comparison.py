"""Validated inputs for annual ferry-line scenario comparisons."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Mass = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False, strict=True)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ActionAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delta_tonnes: Mass
    source: Source


class ActionInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserve_ferry: ActionAssumption
    additional_ferry: ActionAssumption


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: Literal["kaliningrad"] = "kaliningrad"
    mode: Literal["sea"] = "sea"
    direction: Literal["inbound", "outbound"]
    period: str = Field(pattern=r"^(201[5-9]|202[0-9]|2030)$")
    load_basis: Literal["direct", "forecast"] = "direct"
    demand_tonnes: Mass | None = None
    forecast_multiplier: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)] | None = None
    demand_source: Source
    capacity_tonnes: Mass
    capacity_source: Source
    actions: ActionInputs
    include_directions: bool = False
    grid_size: int = Field(default=9, ge=3, le=15, strict=True)

    @field_validator("include_directions", mode="before")
    @classmethod
    def boolean_only(cls, value):
        if type(value) is not bool:
            raise ValueError("Выбор направлений должен быть логическим значением")
        return value

    @model_validator(mode="after")
    def compatible_inputs(self) -> Self:
        if self.load_basis == "direct":
            if self.demand_tonnes is None or self.forecast_multiplier is not None:
                raise ValueError("Для прямого ввода задайте нагрузку в тоннах без множителя прогноза")
        elif self.demand_tonnes is not None or self.forecast_multiplier is None or int(self.period) < 2024:
            raise ValueError("Для масштаба прогноза задайте множитель и год 2024–2030 без прямой нагрузки")
        for action in (self.actions.reserve_ferry, self.actions.additional_ferry):
            if self.capacity_tonnes + action.delta_tonnes > 1e12:
                raise ValueError("Итоговая провозная возможность превышает предел 1e12 тонн")
        return self
