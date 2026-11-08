"""HTTP client for the real application factory."""

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings


@pytest.fixture
def client() -> TestClient:
    settings = Settings(_env_file=None, environment="test", cors_origins=["http://localhost:5173"])
    return TestClient(create_app(settings))
