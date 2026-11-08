"""Availability must reflect missing integrations."""

from fastapi.testclient import TestClient

from backend.schemas.status import StatusResponse


def test_status(client: TestClient) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = StatusResponse.model_validate(response.json())
    assert body.components.model_dump() == {
        "backend": "ready",
        "data": "not_connected",
        "ml": "not_connected",
        "scenarios": "not_connected",
    }
    assert body.capabilities.model_dump() == {"historical_data": False, "forecast": False, "scenario_analysis": False}
