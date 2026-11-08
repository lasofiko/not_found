"""Component readiness schemas."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ComponentState(StrEnum):
    ready = "ready"
    not_connected = "not_connected"
    unavailable = "unavailable"


class Components(BaseModel):
    backend: Literal[ComponentState.ready]
    data: ComponentState
    ml: ComponentState
    scenarios: ComponentState


class Capabilities(BaseModel):
    historical_data: bool
    forecast: bool
    scenario_analysis: bool


class StatusResponse(BaseModel):
    components: Components
    capabilities: Capabilities
