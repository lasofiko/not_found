"""Current connection status; future integrations replace this explicit state."""

from fastapi import APIRouter, Request

from backend.schemas.status import Capabilities, Components, ComponentState, StatusResponse

router = APIRouter(tags=["Infrastructure"])


@router.get("/status", response_model=StatusResponse, summary="Получить состояние компонентов")
def get_status(request: Request) -> StatusResponse:
    ml_state = ComponentState.not_connected
    try:
        request.app.state.forecast_service.get("inbound", 2024)
        ml_state = ComponentState.ready
    except FileNotFoundError:
        pass
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        ml_state = ComponentState.unavailable
    return StatusResponse(
        components=Components(
            backend=ComponentState.ready,
            data=ComponentState.not_connected,
            ml=ml_state,
            scenarios=ComponentState.not_connected,
        ),
        capabilities=Capabilities(
            historical_data=False,
            forecast=ml_state == ComponentState.ready,
            scenario_analysis=False,
        ),
    )
