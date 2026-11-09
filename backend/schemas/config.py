"""Project configuration, separate from current availability."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Direction(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class TransportMode(StrEnum):
    sea = "sea"
    rail = "rail"
    road = "road"
    air = "air"
    all = "all"


class MassUnit(StrEnum):
    tonnes = "tonnes"
    thousand_tonnes = "thousand_tonnes"


class Granularity(StrEnum):
    month = "month"
    quarter = "quarter"
    year = "year"


class Horizon(BaseModel):
    working_target_year: Literal[2030] = 2030
    conditional_extension_year: Literal[2035] = 2035


class SupportedOptions(BaseModel):
    directions: list[Direction]
    transport_modes: list[TransportMode]
    mass_units: list[MassUnit]
    granularities: list[Granularity]
    horizon: Horizon


class AvailableOptions(BaseModel):
    history_granularities: list[Granularity]
    forecast_granularities: list[Granularity]
    forecast_target_years: list[int]


class ConfigResponse(BaseModel):
    supported: SupportedOptions
    available: AvailableOptions
