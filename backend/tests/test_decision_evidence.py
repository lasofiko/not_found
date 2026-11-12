"""Offline synthetic evidence checks, no fitting or blind-data access."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.v1.decision_evidence import router
from backend.decision_evidence import ASYMMETRY_WARNING, build_coverage, build_direction_profile, public_url
from backend.forecast_service import ForecastService
from pipeline.common import digest, write_json


@pytest.fixture
def annual_service(tmp_path):
    artifact = json.loads((Path(__file__).parent / "fixtures/synthetic-annual-model.json").read_text(encoding="utf-8"))
    artifact["directions"]["inbound"]["values"] = [150.0] * 9
    artifact["directions"]["outbound"]["values"] = [50.0] * 9
    path = tmp_path / "models/synthetic-only.json"
    write_json(path, artifact)
    sha = digest(path.read_bytes())
    path.with_suffix(".json.sha256").write_text(sha, encoding="utf-8")
    write_json(tmp_path / "models/latest.json", {
        "model_path": "models/synthetic-only.json", "model_version": artifact["model_version"], "sha256": sha,
    })
    return ForecastService(tmp_path), path, artifact


def test_history_forecast_methods_and_passports_share_artifact(annual_service):
    service, _, _ = annual_service
    result = build_direction_profile(service, 2025)
    assert result["status"] == "ready"
    assert result["model_version"] == "synthetic-http-test-only"
    assert result["forecast_origin"] == 2023
    assert [r["period"] for r in result["rows"]] == [str(y) for y in range(2015, 2026)]
    observed = result["rows"][0]
    assert observed == {
        "period": "2015", "result_type": "observed", "inbound": 150, "outbound": 50,
        "total": 200, "inbound_share": 0.75, "outbound_share": 0.25, "difference_tonnes": 100,
        "inbound_outbound_ratio": 3,
    }
    forecast = result["rows"][9]
    assert forecast["period"] == "2024" and forecast["result_type"] == "forecast"
    assert forecast["inbound"] == 100 and forecast["outbound"] == 50
    assert forecast["total"] == 150
    assert "interval" not in forecast
    assert ASYMMETRY_WARNING in result["warnings"]
    for index, row in enumerate(result["rows"]):
        for field in ("inbound", "outbound", "total", "inbound_share", "outbound_share", "difference_tonnes",
                      "inbound_outbound_ratio"):
            passport = result["passports"][f"rows.{index}.{field}"]
            assert passport["value"] == row[field]
            assert passport["period"] == row["period"]
            assert passport["model_version"] == result["model_version"]
            assert passport["calculated_at"] == result["checked_at"]
    assert result["passports"]["rows.9.total"]["formula"] == "inbound + outbound"
    assert "local_path" not in json.dumps(result)


def test_no_model_does_not_invent_zeros(tmp_path):
    result = build_direction_profile(ForecastService(tmp_path))
    assert result["status"] == "not_connected"
    assert all(r["inbound"] is None and r["outbound"] is None and r["total"] is None for r in result["rows"])
    assert result["passports"]["rows.0.inbound"]["human_review"] is None
    assert "отсутствует" in result["passports"]["rows.0.inbound"]["machine_validation"]


def test_partial_direction_and_zero_denominators(forecast_context):
    def get(direction, end_year):
        if direction == "outbound":
            raise FileNotFoundError("synthetic missing")
        return forecast_context
    result = build_direction_profile(SimpleNamespace(get=get))
    assert result["status"] == "partial"
    assert result["rows"][9]["inbound"] == 12345
    assert result["rows"][9]["outbound"] is None
    assert result["rows"][9]["difference_tonnes"] is None

    def zero(direction, end_year):
        item = forecast_context.model_copy(deep=True, update={"direction": direction})
        for point in item.predictions:
            point.value = 0
        return item
    result = build_direction_profile(SimpleNamespace(get=zero))
    assert result["rows"][9]["total"] == 0
    assert result["rows"][9]["inbound_share"] is None
    assert result["rows"][9]["outbound_share"] is None
    assert result["rows"][9]["inbound_outbound_ratio"] is None


def test_incompatible_model_versions_rejected(forecast_context):
    def get(direction, end_year):
        return forecast_context.model_copy(update={"direction": direction, "model_version": direction})
    with pytest.raises(ValueError, match="Версии"):
        build_direction_profile(SimpleNamespace(get=get))


def test_history_switch_or_corruption_rejected(annual_service):
    service, path, artifact = annual_service
    assert build_direction_profile(service)["status"] == "ready"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        build_direction_profile(service)
    # Reset only the temporary synthetic copy with invalid saved historical mass.
    damaged = copy.deepcopy(artifact)
    damaged["directions"]["inbound"]["values"][0] = -1
    write_json(path, damaged)
    sha = digest(path.read_bytes())
    path.with_suffix(".json.sha256").write_text(sha, encoding="utf-8")
    pointer = json.loads((path.parent / "latest.json").read_text(encoding="utf-8"))
    write_json(path.parent / "latest.json", pointer | {"sha256": sha})
    with pytest.raises(ValueError, match="масса"):
        build_direction_profile(service)


def test_coverage_separates_registered_missing_original_and_connected_model(annual_service):
    service, _, _ = annual_service
    source = {
        "source_id": "synthetic_test_fixture", "source_key": "rosmorport_freight", "organization": "Синтетика",
        "url": "https://example.org/source", "local_path": "data/raw/missing.html",
    }
    write_json(service.root / "data/source_registry.json", {"artifacts": [source]})
    result = build_coverage(service)
    assert {r["territory"] for r in result["rows"]} == {
        "Калининградская область", "Приморский край", "Камчатский край",
    }
    line = next(r for r in result["rows"] if r["id"] == "line_inbound")
    assert line["stages"] == {
        "source_registered": True, "original_available": False, "extracted": True, "series_checked": True,
        "model_exists": True, "model_connected": True, "ui_available": True,
    }
    assert line["forecast_available"] is True
    assert all(r["forecast_available"] is False for r in result["rows"] if not r["id"].startswith("line_"))
    assert "local_path" not in json.dumps(result)
    assert "C:\\" not in json.dumps(result)
    assert next(r for r in result["rows"] if r["id"] == "vladivostok_total")["direction"] is None


def test_coverage_does_not_mark_existing_csv_as_checked_without_manifest(tmp_path):
    relative = "data/processed/port_protocol/vladivostok_total/training_series.csv"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("synthetic-data-only", encoding="utf-8")
    source = {"source_id": "synthetic-port", "dataset_kind": "port_cabotage", "url": "provided:local.xls"}
    write_json(tmp_path / "data/source_registry.json", {"artifacts": [source]})
    result = build_coverage(ForecastService(tmp_path))
    port = next(r for r in result["rows"] if r["id"] == "vladivostok_total")
    assert port["stages"]["extracted"] is True
    assert port["stages"]["series_checked"] is None
    assert port["source"]["url"] is None


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/secret", "http://localhost/api", "http://127.0.0.1/",
                                 "https://user:secret@example.org/", "https://server.local/", "provided:original.xls"])
def test_unsafe_evidence_urls_are_not_exposed(url):
    assert public_url(url) is None


def test_profile_http_prefix_and_unavailable_region(annual_service):
    app = FastAPI()
    app.state.forecast_service = annual_service[0]
    app.include_router(router, prefix="/custom/v1")
    with TestClient(app) as client:
        assert client.get("/custom/v1/direction-profile").json()["status"] == "ready"
        response = client.get("/custom/v1/direction-profile", params={"region": "kamchatka"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PROFILE_NOT_AVAILABLE"
        assert client.get("/custom/v1/direction-profile", params={"end_year": 2031}).status_code == 422
        assert len(client.get("/custom/v1/coverage").json()["rows"]) == 8
