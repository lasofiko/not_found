"""HTTP client for the real application factory."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.forecast_service import ForecastService
from backend.schemas.forecast import ForecastResponse


@pytest.fixture
def forecast_context() -> ForecastResponse:
    """Synthetic artifact for contract tests, never a production data source."""
    score = {"MAE": 10, "WAPE": 5, "observations": 2}
    return ForecastResponse(
        direction="inbound",
        mode="sea",
        unit="tonnes",
        forecast_origin=2023,
        observations=9,
        geography={
            "origin": "ust_luga",
            "destination": "kaliningrad_port",
            "scope": "Только линия Усть-Луга — Балтийск",
            "route_id": "ust_luga_kaliningrad_ferry",
        },
        training_period={"start": "2015", "end": "2023"},
        selected_model="naive",
        data_version="test-data",
        model_version="test-model",
        generated_at="2026-11-11T00:00:00Z",
        predictions=[
            {
                "period": str(year),
                "period_granularity": "year",
                "horizon_years": year - 2023,
                "value": 12345,
                "interval": {"80": {"lower": 12000, "upper": 13000}, "95": {"lower": 11000, "upper": 14000}},
            }
            for year in range(2024, 2031)
        ],
        provenance={
            "source_ids": ["test-source"],
            "source_locators": ["test-cell"],
            "sources": [
                {"source_id": "test-source", "organization": "Тестовый источник", "url": "https://example.org/test"}
            ],
        },
        metrics={
            "selected": {"development": score, "holdout": score},
            "baseline": {"development": score, "holdout": score},
            "selection_period": "2020–2021",
            "holdout_period": "2022–2023",
            "evaluation_horizon_years": 1,
            "methodological_status": "experimental",
        },
        warnings=["Тестовый прогноз; охват не равен всему коридору."],
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, environment="test", cors_origins=["http://localhost:5173"])
    app = create_app(settings)
    app.state.forecast_service = ForecastService(tmp_path)
    return TestClient(app)
