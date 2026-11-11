"""HTTP client for the real application factory."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.forecast_service import ForecastService


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, environment="test", cors_origins=["http://localhost:5173"])
    app = create_app(settings)
    app.state.forecast_service = ForecastService(tmp_path)
    return TestClient(app)
