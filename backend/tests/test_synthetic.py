"""Контракт изолированной синтетики; числа ниже только локальные тестовые фикстуры."""

import csv
import io

import pytest

from backend.synthetic_data import SyntheticStore, file_sha
from pipeline.synthetic import BASE_FIELDS, SCENARIO_FIELDS
from pipeline.synthetic_audit import save_csv, save_json


@pytest.fixture
def bundle(tmp_path, client):
    rows = []
    for sid, region, offset in (("test_a", "kaliningrad", 0), ("test_b", "kamchatka", 100)):
        for method in ("rw_bootstrap", "rw_block_bootstrap"):
            for real in (1, 2):
                for month in (1, 2):
                    rows.append(
                        dict(
                            run_id="test-run",
                            series_id=sid,
                            region=region,
                            indicator="port_cabotage_handling",
                            mode="sea",
                            direction=None,
                            cargo_group="Тестовая группа",
                            period=f"2026-{month:02d}",
                            frequency="month",
                            unit="tonnes",
                            value=offset + real * month,
                            status="synthetic",
                            result_type="conditional_simulation",
                            generator_id=method,
                            model_version="test-model",
                            data_version="test-data",
                            scenario_id="baseline",
                            realization_id=real,
                            seed=42,
                            training_start="2023-01",
                            training_end="2025-12",
                            source_ids_json='["test-source"]',
                            provenance_id="test-provenance",
                        )
                    )
    pending = [
        {
            **rows[0],
            "status": "scenario",
            "result_type": "scenario_sensitivity",
            "scenario_id": "stress",
            "value": None,
            "realization_id": None,
            "scenario_status": "requires_input",
            "throughput_multiplier": None,
            "assumption_source": None,
        }
    ]
    save_csv(tmp_path / "synthetic_timeseries.csv", rows, BASE_FIELDS)
    save_csv(tmp_path / "synthetic_scenarios.csv", pending, SCENARIO_FIELDS)
    save_json(tmp_path / "provenance.json", {"sources": [{"source_id": "test-source", "status": "test_fixture"}]})
    manifest = {
        "schema_version": "0.1",
        "run_id": "test-run",
        "data_version": "test-data",
        "validation": {"status": "passed"},
        "limitations": ["Только тестовая фикстура"],
        "training_end": "2025-12",
        "available_asof": "2026-01-31",
        "series": [{"series_id": "test_a"}, {"series_id": "test_b"}],
        "generator_states": [
            {"series_id": "test_a", "generator_id": "rw_bootstrap", "model_version": "test-model", "seed": 42}
        ],
        "selected_generators": {"test_a": "rw_bootstrap"},
        "scenarios": {
            "baseline": {"throughput_multiplier": 1, "assumption_source": "Тест"},
            "stress": {"throughput_multiplier": None, "assumption_source": None},
        },
        "outputs": {
            n: file_sha(tmp_path / n)
            for n in ("synthetic_timeseries.csv", "synthetic_scenarios.csv", "provenance.json")
        },
    }
    save_json(tmp_path / "generation_manifest.json", manifest)
    client.app.state.synthetic_store = SyntheticStore(tmp_path)
    return tmp_path, rows, manifest


def test_not_connected_does_not_affect_health_or_forecast(client):
    assert client.get("/api/v1/synthetic/catalog").status_code == 503
    assert client.get("/api/v1/synthetic/catalog").json()["error"]["code"] == "SYNTHETIC_NOT_CONNECTED"
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/forecasts").json()["status"] == "not_connected"


def test_filter_realization_and_export_share_values(client, bundle):
    query = "series_id=test_a&generator_id=rw_bootstrap&realization_id=1"
    data = client.get("/api/v1/synthetic/series?" + query).json()
    assert data["total"] == 2
    assert [r["value"] for r in data["rows"]] == [1, 2]
    assert all(r["direction"] is None and r["status"] == "synthetic" for r in data["rows"])
    exported = client.get("/api/v1/synthetic/export?format=json&" + query).json()
    assert exported["rows"] == data["rows"]
    csv_response = client.get("/api/v1/synthetic/export?format=csv&" + query)
    records = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert [float(r["value"]) for r in records] == [1, 2]
    assert all(r["provenance_id"] and r["warnings_json"] for r in records)
    assert data["model_versions"] == ["test-model"]


def test_summary_keeps_groups_methods_and_missing_scenarios_separate(client, bundle):
    response = client.get("/api/v1/synthetic/summary?series_id=test_a").json()
    assert len(response["rows"]) == 4
    jan = [r for r in response["rows"] if r["period"] == "2026-01"]
    assert all(r["n"] == 2 and r["mean"] == 1.5 and r["variance"] == 0.5 for r in jan)
    assert all(r["q10"] == pytest.approx(1.1) and r["q90"] == pytest.approx(1.9) for r in jan)
    pending = client.get("/api/v1/synthetic/summary?scenario_id=stress").json()
    assert pending["rows"][0]["n"] == 0
    assert pending["rows"][0]["missing_n"] == 1
    assert pending["rows"][0]["mean"] is None
    assert pending["status"] == "scenario"
    assert any("требует" in w for w in pending["warnings"])
    empty = client.get("/api/v1/synthetic/series?series_id=not_present").json()
    assert empty["total"] == 0 and empty["rows"] == []
    assert any("не означает нулевой" in w for w in empty["warnings"])


@pytest.mark.parametrize(
    "query", ["limit=0", "realization_id=101", "region=other", "scenario_id=unknown", "generator_id=ctgan"]
)
def test_rejects_invalid_filters(client, bundle, query):
    assert client.get("/api/v1/synthetic/series?" + query).status_code == 422


def test_integrity_is_rechecked_after_cache_hit(client, bundle):
    path, _, _ = bundle
    assert client.get("/api/v1/synthetic/catalog").status_code == 200
    with (path / "synthetic_timeseries.csv").open("a") as stream:
        stream.write("corrupted\n")
    response = client.get("/api/v1/synthetic/series")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SYNTHETIC_INVALID"


@pytest.mark.parametrize("change", ["observed", "teu", "duplicate", "outside"])
def test_schema_violations_rejected_even_with_updated_checksum(client, bundle, change):
    path, rows, manifest = bundle
    if change == "outside":
        manifest["outputs"]["../outside.csv"] = "0" * 64
    else:
        if change == "observed":
            rows[0]["status"] = "observed"
        elif change == "teu":
            rows[0]["unit"] = "teu"
        else:
            rows.append(rows[0])
        save_csv(path / "synthetic_timeseries.csv", rows, BASE_FIELDS)
        manifest["outputs"]["synthetic_timeseries.csv"] = file_sha(path / "synthetic_timeseries.csv")
    save_json(path / "generation_manifest.json", manifest)
    assert client.get("/api/v1/synthetic/catalog").status_code == 500


def test_comparison_and_provenance_preserve_origin(client, bundle):
    data = client.get("/api/v1/synthetic/compare?region=kamchatka").json()
    assert all(r["region"] == "kamchatka" for r in data["rows"])
    assert {r["generator_id"] for r in data["rows"]} == {"rw_bootstrap", "rw_block_bootstrap"}
    assert client.get("/api/v1/synthetic/provenance").json()["provenance"]["sources"][0]["source_id"] == "test-source"
