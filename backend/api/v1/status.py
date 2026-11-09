"""Current connection status; future integrations replace this explicit state."""

from fastapi import APIRouter

from backend.schemas.status import Capabilities, Components, ComponentState, StatusResponse

router = APIRouter(tags=["Infrastructure"])


@router.get("/status", response_model=StatusResponse, summary="Получить состояние компонентов")
def get_status() -> StatusResponse:
    return StatusResponse(
        components=Components(
            backend=ComponentState.ready,
            data=ComponentState.not_connected,
            ml=ComponentState.not_connected,
            scenarios=ComponentState.not_connected,
        ),
        capabilities=Capabilities(
            historical_data=False,
            forecast=False,
            scenario_analysis=False,
        ),
    )
