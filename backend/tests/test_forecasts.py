"""Контракт прогноза и интеграция с предоставленным модельным комплектом."""

import json
import os
import shutil
from pathlib import Path

import pytest

from backend.forecast_service import ForecastService
from pipeline.common import ROOT


@pytest.mark.parametrize("query", ["region=primorye", "direction=both", "mode=road"])
def test_unsupported_slice(client, query):
    response = client.get("/api/v1/forecasts?" + query)
    assert response.status_code == 404
    assert response.json()["status"] == "not_available"


def test_missing_model_and_invalid_year(client):
    response = client.get("/api/v1/forecasts")
    assert response.status_code == 503
    assert response.json()["status"] == "not_connected"
    assert "predictions" not in response.json()
    assert client.get("/api/v1/forecasts?end_year=2031").status_code == 422
    assert client.get("/api/v1/forecasts?end_year=2026-01").status_code == 422
    assert client.get("/api/v1/health").status_code == 200


@pytest.fixture
def provided_model(client, tmp_path: Path):
    if os.environ.get("RUN_REAL_MODEL_TESTS") != "1":
        pytest.skip("Реальные модели проверяются отдельно с RUN_REAL_MODEL_TESTS=1; обычный CI использует синтетику")
    pointer_path = ROOT / "models/latest.json"
    if not pointer_path.exists():
        pytest.skip("Интеграционный тест требует предоставленный реальный комплект models")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    for name in ("models/latest.json", pointer["model_path"], pointer["model_path"] + ".sha256"):
        source = (ROOT / name).resolve()
        assert source.is_relative_to(ROOT / "models")
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    client.app.state.forecast_service = ForecastService(tmp_path)
    return pointer, tmp_path


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_real_forecast_matches_original_export(client, provided_model, direction):
    pointer, _ = provided_model
    expected = json.loads((ROOT / pointer["report_path"] / "forecast.json").read_text(encoding="utf-8"))[direction]
    response = client.get("/api/v1/forecasts", params={"direction": direction})
    assert response.status_code == 200
    actual = response.json()
    assert actual["status"] == "ready"
    assert actual["predictions"] == expected["predictions"]
    assert actual["provenance"] == expected["provenance"]
    assert (actual["model_version"], actual["data_version"]) == (expected["model_version"], expected["data_version"])
    assert client.get("/api/v1/status").json()["capabilities"]["forecast"] is True
    assert client.get("/api/v1/config").json()["available"]["forecast_granularities"] == ["year"]
    short = client.get("/api/v1/forecasts", params={"end_year": 2025, "direction": direction}).json()
    assert short["predictions"] == expected["predictions"][:2]


def test_damaged_model_is_an_error(client, provided_model):
    pointer, root = provided_model
    with (root / pointer["model_path"]).open("ab") as stream:
        stream.write(b" ")
    response = client.get("/api/v1/forecasts")
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert "predictions" not in response.json()
    assert client.get("/api/v1/status").json()["capabilities"]["forecast"] is False


def test_registry_and_capacity(client):
    actions = {row["id"]: row for row in client.get("/api/v1/scenarios").json()}
    # Решение 12.11.2026: Б — сокращение портовой части оборота, В — постоянный дополнительный паром.
    assert actions["port_turnaround"]["label"] == "Б"
    assert actions["additional_ferry"]["label"] == "В"
    values = dict(
        period="2026-11",
        demand_tonnes=120,
        capacity_tonnes=100,
        capacity_delta_tonnes=10,
        assumption_source="Контрольный ввод",
    )
    body = client.post("/api/v1/scenarios/capacity", json=values).json()
    assert [
        body[key] for key in ("baseline_shortfall_tonnes", "scenario_shortfall_tonnes", "shortfall_reduction_tonnes")
    ] == [20, 10, 10]
    assert client.post("/api/v1/scenarios/capacity", json=values | {"demand_tonnes": True}).status_code == 422
    assert client.post("/api/v1/scenarios/capacity", json=values | {"assumption_source": "   "}).status_code == 422
