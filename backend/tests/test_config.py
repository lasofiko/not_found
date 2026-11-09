"""Project configuration and environment validation."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.core.settings import Settings
from backend.schemas.config import ConfigResponse


def test_config(client: TestClient) -> None:
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    body = ConfigResponse.model_validate(response.json())
    assert body.supported.directions == ["inbound", "outbound"]
    assert body.supported.transport_modes == ["sea", "rail", "road", "air", "all"]
    assert body.supported.mass_units == ["tonnes", "thousand_tonnes"]
    assert body.supported.granularities == ["month", "quarter", "year"]
    assert body.supported.horizon.working_target_year == 2030
    assert body.supported.horizon.conditional_extension_year == 2035
    assert body.available.history_granularities == []
    assert body.available.forecast_granularities == []
    assert body.available.forecast_target_years == []


@pytest.mark.parametrize(
    "override",
    [
        {"api_prefix": "api/v1"},
        {"cors_origins": ["*"]},
        {"log_level": "VERBOSE"},
        {"environment": "unknown"},
    ],
)
def test_invalid_settings_fail_at_startup(override: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **override)
