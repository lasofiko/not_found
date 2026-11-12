"""Контракт месячных прогнозов порта для сайта. Артефакт модели — синтетический, только для теста."""

import json
from pathlib import Path

import pytest

from backend.port_forecast_service import PortForecastService
from ml.port_evaluation import load_port_protocol
from pipeline.common import ROOT, digest
from pipeline.model_inputs import month_index, month_period
from pipeline.port_protocol import TARGETS


def write_model(
    root: Path, target_id: str, model_version: str, selected: str = "ses", external: dict | None = None
) -> None:
    port, cargo = TARGETS[target_id]
    predictions = []
    for i in range(60):
        value = 150000.0 + i
        predictions.append(
            {
                "period": month_period(month_index("2026-01") + i),
                "horizon_months": i + 1,
                "value": value,
                "experimental": i >= 12,
                "interval": {
                    "80": {"lower": value - 10000, "upper": value + 10000},
                    "95": {"lower": value - 20000, "upper": value + 20000},
                },
            }
        )
    history = [100000.0 + 1000 * i for i in range(43)]
    artifact = {
        "schema_version": "0.2",
        "target_id": target_id,
        "port": port,
        "cargo_group": cargo,
        "protocol": load_port_protocol(),
        "forecast_origin": "2025-12",
        "training_start": "2022-06",
        "training_data_available_by": "2026-01-31",
        "observations": 43,
        "selected_model": selected,
        "state": {"model_id": selected, "history": history, "feature_names": []},
        "metrics": {},
        "predictions": predictions,
        "warnings": ["Синтетический тестовый артефакт."],
        "data_version": "test-data",
        "model_version": model_version,
        "generated_at": "2026-11-12T00:00:00Z",
        "provenance": {"sources": [{"source_id": "synthetic-test-only"}]},
    }
    if external is not None:
        artifact.pop("protocol")
        artifact.update(schema_version="0.3", selected_model="ridge_ar3_10", external=external)
    folder = root / "models/port" / target_id / model_version
    folder.mkdir(parents=True)
    body = json.dumps(artifact, ensure_ascii=False).encode()
    (folder / "model.json").write_bytes(body)
    (folder / "model.json.sha256").write_text(digest(body), encoding="utf-8")
    pointer = {
        "model_path": f"models/port/{target_id}/{model_version}/model.json",
        "model_version": model_version,
        "sha256": digest(body),
    }
    (root / "models/port" / target_id / "latest.json").write_text(json.dumps(pointer), encoding="utf-8")


EXTERNAL = {
    "code_sha256": "a" * 64,
    "protocol_sha256": "b" * 64,
    "selection_lock_sha256": "c" * 64,
    "treatment": "original_only",
}


@pytest.fixture
def port_client(client, tmp_path):
    client.app.state.port_forecast_service = PortForecastService(root=tmp_path, evidence_root=ROOT)
    return client, tmp_path


def test_catalog_uses_jury_metrics(port_client):
    client, _ = port_client
    body = client.get("/api/v1/port-forecasts").json()
    assert body["primary_target"] == "kaliningrad_ferries"
    targets = {t["target_id"]: t for t in body["targets"]}
    assert set(targets) == set(TARGETS)
    ferries = targets["kaliningrad_ferries"]
    assert ferries["display"] == "primary" and ferries["model_connected"] is False
    assert ferries["quality"]["selected_model"] == "ridge_ar3_10"
    assert ferries["quality"]["blind_verdict"] == "better_than_naive_in_both"
    assert targets["kaliningrad_total"]["quality"]["selected_model"] == "ses"
    assert targets["kaliningrad_total"]["display"] == "shown"
    assert targets["vladivostok_total"]["quality"]["blind_verdict"] != "better_than_naive_in_both"
    assert targets["petropavlovsk_total"]["display"] == "history_only"


def test_missing_model_is_explicit(port_client):
    client, _ = port_client
    response = client.get("/api/v1/port-forecasts/kaliningrad_ferries")
    assert response.status_code == 503
    assert response.json()["status"] == "not_connected"


def test_forecast_from_verified_external_artifact(port_client):
    client, root = port_client
    write_model(root, "kaliningrad_ferries", "bb-ridge-d9c6acb34f45", external=EXTERNAL)
    body = client.get("/api/v1/port-forecasts/kaliningrad_ferries", params={"end_period": "2026-06"}).json()
    assert body["display"] == "primary" and body["model_version"] == "bb-ridge-d9c6acb34f45"
    assert body["selected_model"] == "ridge_ar3_10"
    assert [p["period"] for p in body["predictions"]] == [f"2026-0{m}" for m in range(1, 7)]
    assert body["history"][0]["period"] == "2022-06" and body["history"][-1]["period"] == "2025-12"
    assert body["quality"]["holdout"]["selected_WAPE"] < body["quality"]["holdout"]["naive_WAPE"]
    assert body["result_type"] == "forecast"
    assert body["direction"] is None and body["geography"]["origin"] is None
    assert body["provenance"]["sources"] == [{"source_id": "synthetic-test-only"}]
    assert body["training_period"] == {"start": "2022-06", "end": "2025-12"}
    assert all(p["status"] == "forecast" for p in body["predictions"])


def test_wrong_model_version_is_rejected(port_client):
    client, root = port_client
    write_model(root, "kaliningrad_ferries", "bb-port-5b1f25b217f5")  # прежняя SES больше не на сайте
    assert client.get("/api/v1/port-forecasts/kaliningrad_ferries").status_code == 500


def test_external_artifact_with_synthetic_treatment_is_rejected(port_client):
    client, root = port_client
    synthetic = {**EXTERNAL, "treatment": "completed_mean"}
    write_model(root, "kaliningrad_ferries", "bb-ridge-d9c6acb34f45", external=synthetic)
    assert client.get("/api/v1/port-forecasts/kaliningrad_ferries").status_code == 500


def test_overridden_total_model(port_client):
    client, root = port_client
    write_model(root, "kaliningrad_total", "bb-port-09047f079455")
    body = client.get("/api/v1/port-forecasts/kaliningrad_total").json()
    assert body["selected_model"] == "ses" and len(body["predictions"]) == 12


def test_history_only_target_has_no_predictions(port_client):
    client, root = port_client
    write_model(root, "petropavlovsk_total", "bb-port-6454be65293a")
    body = client.get("/api/v1/port-forecasts/petropavlovsk_total").json()
    assert body["predictions"] == [] and len(body["history"]) == 43
    assert body["result_type"] == "history"


@pytest.mark.parametrize(
    "path, code",
    [
        ("/api/v1/port-forecasts/unknown", 404),
        ("/api/v1/port-forecasts/kaliningrad_ferries?end_period=2031-01", 422),
        ("/api/v1/port-forecasts/kaliningrad_ferries?end_period=2026-1", 422),
    ],
)
def test_bad_requests(port_client, path, code):
    client, _ = port_client
    assert client.get(path).status_code == code
