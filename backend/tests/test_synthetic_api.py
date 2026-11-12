"""Изолированные тестовые фикстуры никогда не подключаются к рабочему API."""

import csv
import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.synthetic_service import FIELDS, SyntheticBundle


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def reseal(path):
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["outputs"] = {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    write_json(path / "manifest.json", manifest)


@pytest.fixture
def test_bundle(tmp_path):
    item = {
        "series_id": "test_only",
        "region": "kaliningrad",
        "metric": "port_cabotage_mass",
        "frequency": "month",
        "direction": "",
        "mode": "sea",
        "cargo_group": "test_only",
        "scope": "Тестовая фикстура, не рабочие данные",
        "forecast_origin": "2025-12",
        "unit": "tonnes",
        "provenance_id": "test_only",
        "methods": ["iid_increment_bootstrap"],
        "realizations": 2,
        "horizon": 2,
        "output_start": "2026-01",
        "output_end": "2026-02",
        "warnings": ["Только тест"],
    }
    rows = []
    for scenario, scale in {"reference": 1, "scale_down": 0.9, "scale_up": 1.1}.items():
        for rid, values in enumerate(([10, 30], [40, 20])):
            for month, value in enumerate(values, 1):
                rows.append(
                    {
                        **{k: v for k, v in item.items() if k in FIELDS},
                        "period": f"2026-{month:02d}",
                        "method": "iid_increment_bootstrap",
                        "scenario_id": scenario,
                        "realization_id": rid,
                        "value": value * scale,
                        "status": "synthetic",
                        "result_type": "scenario_ensemble",
                        "data_version": "test-data",
                        "generator_version": "test-generator",
                    }
                )
    with (tmp_path / "synthetic.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        tmp_path / "catalog.json",
        {
            "schema_version": "1.0",
            "unit": "tonnes",
            "status": "synthetic",
            "result_type": "scenario_ensemble",
            "data_version": "test-data",
            "generator_version": "test-generator",
            "warnings": ["Только тест"],
            "scenarios": {"reference": 1, "scale_down": 0.9, "scale_up": 1.1},
            "rows": len(rows),
            "series": [item],
        },
    )
    write_json(tmp_path / "provenance.json", {"test_only": {"source_ids": ["test_fixture_only"]}})
    write_json(tmp_path / "recommendations.json", [{"series_id": "test_only", "selected_method": None}])
    write_json(tmp_path / "config.json", {})
    for name in ("metrics.csv", "diagnostics.csv", "backtest.csv", "reference/real_series.csv"):
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True)
        target.write_text("series_id,method\n")
    write_json(
        tmp_path / "manifest.json",
        {"schema_version": "1.0", "data_version": "test-data", "generator_version": "test-generator", "outputs": {}},
    )
    reseal(tmp_path)
    return tmp_path


def client_for(path=""):
    return TestClient(create_app(Settings(_env_file=None, environment="test", synthetic_bundle_path=str(path))))


def test_disabled_by_default_and_health_remains_available():
    client = client_for()
    response = client.get("/api/v1/synthetic/catalog")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SYNTHETIC_NOT_CONFIGURED"
    assert client.get("/api/v1/health").status_code == 200


def test_filters_provenance_realization_and_export(test_bundle):
    client = client_for(test_bundle)
    params = {"region": "kaliningrad", "metric": "port_cabotage_mass", "scenario_id": "reference"}
    response = client.get("/api/v1/synthetic/realizations", params={**params, "realization_id": 0})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2 and result["provenance_ids"] == ["test_only"]
    assert [r["value"] for r in result["data"]] == [10, 30]
    assert all(r["direction"] == "" and r["status"] == "synthetic" for r in result["data"])
    assert client.get("/api/v1/synthetic/provenance", params=params).json()["data"]["test_only"]["source_ids"]
    exported = client.get("/api/v1/synthetic/export", params=params)
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 4 and {r["status"] for r in rows} == {"synthetic"}
    assert client.get("/api/v1/synthetic/export", params={**params, "format": "json"}).json()["data"]
    assert client.get("/api/v1/synthetic/compare", params=params).json()["data"]["evaluation_scenario"] == "reference"


def test_horizon_summary_uses_path_totals(test_bundle):
    client = client_for(test_bundle)
    response = client.get("/api/v1/synthetic/summary", params={"scenario_id": "reference", "aggregation": "horizon"})
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["mean"] == 50 and data[0]["std"] == 10
    assert data[0]["p05"] == pytest.approx(41) and data[0]["p95"] == pytest.approx(59)


@pytest.mark.parametrize("query", ["realization_id=-1", "limit=0", "offset=-2", "limit=10001"])
def test_bad_query_rejected(test_bundle, query):
    assert client_for(test_bundle).get("/api/v1/synthetic/realizations?" + query).status_code == 422


def test_missing_slice_and_pagination(test_bundle):
    client = client_for(test_bundle)
    assert client.get("/api/v1/synthetic/catalog?region=unknown").status_code == 404
    response = client.get("/api/v1/synthetic/realizations?offset=11&limit=1").json()
    assert response["total"] == 12 and len(response["data"]) == 1


@pytest.mark.parametrize("defect", ["status", "unit", "nan", "duplicate", "missing", "calendar", "scenario"])
def test_resealed_invalid_content_is_still_rejected(test_bundle, defect):
    path = test_bundle / "synthetic.csv"
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    if defect == "duplicate":
        rows[1] = dict(rows[0])
    elif defect == "missing":
        rows.pop()
    else:
        key, value = {
            "status": ("status", "observed"),
            "unit": ("unit", "TEU"),
            "nan": ("value", "NaN"),
            "calendar": ("period", "2026-13"),
            "scenario": ("value", "999"),
        }[defect]
        rows[0][key] = value
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    reseal(test_bundle)
    with pytest.raises((ValueError, KeyError)):
        SyntheticBundle(test_bundle)
    response = client_for(test_bundle).get("/api/v1/synthetic/catalog")
    assert response.status_code == 503 and response.json()["error"]["code"] == "SYNTHETIC_INVALID"


def test_sha256_mismatch_rejected(test_bundle):
    with (test_bundle / "synthetic.csv").open("a") as stream:
        stream.write("\n")
    assert client_for(test_bundle).get("/api/v1/synthetic/catalog").status_code == 503
