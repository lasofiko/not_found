"""Проверка настоящего синтетического комплекта через ASGI без сетевого сервера."""

import argparse
import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.synthetic_service import SyntheticBundle


def verify(path):
    bundle = SyntheticBundle(path)
    settings = Settings(_env_file=None, environment="test", api_prefix="/check/v1", synthetic_bundle_path=str(path))
    client = TestClient(create_app(settings))
    base = "/check/v1/synthetic"
    check = {"series_id": "kaliningrad_ferries", "method": "block_increment_bootstrap", "scenario_id": "reference"}
    catalog = client.get(base + "/catalog?region=kaliningrad")
    assert catalog.status_code == 200
    assert catalog.json()["data_version"] == bundle.catalog["data_version"]
    result = client.get(base + "/realizations", params={**check, "realization_id": 0})
    assert result.status_code == 200
    assert result.json()["total"] == 12
    assert {r["status"] for r in result.json()["data"]} == {"synthetic"}
    assert {r["direction"] for r in result.json()["data"]} == {""}
    summary = client.get(base + "/summary", params={**check, "aggregation": "horizon"})
    assert summary.status_code == 200
    totals = defaultdict(float)
    for row in bundle.filtered(**check):
        totals[row["realization_id"]] += row["value"]
    assert abs(summary.json()["data"][0]["mean"] - mean(totals.values())) < 1e-6
    provenance = client.get(base + "/provenance", params=check)
    assert provenance.status_code == 200
    assert provenance.json()["data"][check["series_id"]]["sources"]
    compare = client.get(base + "/compare", params={"series_id": check["series_id"]})
    assert compare.status_code == 200
    assert any(r["method"] == "naive" for r in compare.json()["data"]["metrics"])
    exported = client.get(base + "/export", params=check)
    assert exported.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(exported.text)))) == 2400
    exported_json = client.get(base + "/export", params={**check, "format": "json"})
    assert exported_json.status_code == 200
    assert len(exported_json.json()["data"]) == 2400
    assert client.get(base + "/realizations?region=missing").status_code == 404
    assert client.get(base + "/realizations?realization_id=-1").status_code == 422
    assert client.get("/check/v1/health").status_code == 200
    disabled = TestClient(create_app(Settings(_env_file=None, environment="test")))
    assert disabled.get("/api/v1/synthetic/catalog").status_code == 503
    return {
        "status": "passed",
        "data_version": bundle.catalog["data_version"],
        "synthetic_rows": len(bundle.rows),
        "series": len(bundle.series),
        "checks": 11,
        "custom_prefix": "/check/v1",
        "real_artifact_used": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
