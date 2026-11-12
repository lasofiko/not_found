"""Mandatory offline HTTP composition with a test-only portable artifact.

No fitting, real data, or replacement of production models is involved.
"""

import copy
import json
from pathlib import Path

import pytest

from backend.forecast_service import ForecastService
from backend.schemas.forecast import ForecastResponse
from ml.data import load_config
from pipeline.common import digest, write_json


@pytest.fixture
def synthetic_model(client, tmp_path):
    artifact = json.loads((Path(__file__).parent / "fixtures/synthetic-annual-model.json").read_text(encoding="utf-8"))
    assert artifact["test_only"] is True
    # Keep the checked target/protocol visible: the fixture never redefines it.
    assert artifact["config"] == load_config()
    path = tmp_path / "models/synthetic-only.json"
    write_json(path, artifact)
    sha = digest(path.read_bytes())
    path.with_suffix(".json.sha256").write_text(sha, encoding="utf-8")
    write_json(
        tmp_path / "models/latest.json",
        {
            "model_path": "models/synthetic-only.json",
            "model_version": artifact["model_version"],
            "sha256": sha,
        },
    )
    client.app.state.forecast_service = ForecastService(tmp_path)
    return artifact, path


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
@pytest.mark.parametrize("end_year", [2024, 2025, 2030])
def test_real_service_api_schema_with_synthetic_artifact(client, synthetic_model, direction, end_year):
    artifact, _ = synthetic_model
    response = client.get("/api/v1/forecasts", params={"direction": direction, "end_year": end_year})
    assert response.status_code == 200
    result = ForecastResponse.model_validate(response.json())
    assert result.result_type == "forecast"
    assert result.direction == direction
    assert result.unit == "tonnes"
    assert result.forecast_origin == 2023
    assert result.model_version == "synthetic-http-test-only"
    assert [p.period for p in result.predictions] == [str(y) for y in range(2024, end_year + 1)]
    assert result.provenance["source_ids"] == ["synthetic_test_fixture"]
    assert result.geography["origin"] == artifact["directions"][direction]["origin"]
    assert result.predictions[0].value == (100 if direction == "inbound" else 50)
    assert "synthetic" in " ".join(result.warnings).lower()
    assert client.get("/api/v1/status").json()["capabilities"]["forecast"] is True


@pytest.mark.parametrize("damage", ["bytes", "sidecar", "pointer", "state", "provenance"])
def test_synthetic_artifact_damage_is_rejected(client, synthetic_model, damage):
    artifact, path = synthetic_model
    if damage == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "sidecar":
        path.with_suffix(".json.sha256").write_text("0" * 64, encoding="utf-8")
    elif damage == "pointer":
        pointer = path.parent / "latest.json"
        doc = json.loads(pointer.read_text(encoding="utf-8"))
        write_json(pointer, doc | {"sha256": "0" * 64})
    else:
        changed = copy.deepcopy(artifact)
        if damage == "state":
            changed["directions"]["inbound"]["state"]["n_observations"] = 8
        else:
            changed["directions"]["inbound"]["source_ids"] = []
        write_json(path, changed)
        sha = digest(path.read_bytes())
        path.with_suffix(".json.sha256").write_text(sha, encoding="utf-8")
        pointer = path.parent / "latest.json"
        write_json(pointer, json.loads(pointer.read_text(encoding="utf-8")) | {"sha256": sha})
    response = client.get("/api/v1/forecasts")
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert "predictions" not in response.json()
