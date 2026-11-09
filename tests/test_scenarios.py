"""Проверка баланса, границ сценария и воспроизводимого отчёта на синтетике."""
import csv
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.scenarios import capacity_balance
from ml.models import fit_model
from ml.sensitivity import generate_report, make_grid, GRID_FIELDS
from pipeline.common import digest, write_json


class BalanceTests(unittest.TestCase):
    def test_added_capacity_stops_reducing_shortfall_after_load_is_covered(self):
        row = capacity_balance(demand_tonnes=1_400_000, capacity_tonnes=1_200_000, capacity_delta_tonnes=300_000)
        self.assertEqual(row["baseline_shortfall_tonnes"], 200_000)
        self.assertEqual(row["scenario_shortfall_tonnes"], 0)
        self.assertEqual(row["shortfall_reduction_tonnes"], 200_000)
        self.assertEqual(row["scenario_spare_capacity_tonnes"], 100_000)

    def test_no_load_and_no_change_have_no_invented_effect(self):
        for demand in (0, 50, 100, 150):
            unchanged = capacity_balance(demand_tonnes=demand, capacity_tonnes=100, capacity_delta_tonnes=0)
            self.assertEqual(unchanged["shortfall_reduction_tonnes"], 0)
            self.assertEqual(unchanged["baseline_shortfall_tonnes"], unchanged["scenario_shortfall_tonnes"])
        spare = capacity_balance(demand_tonnes=50, capacity_tonnes=100, capacity_delta_tonnes=300)
        self.assertEqual(spare["shortfall_reduction_tonnes"], 0)
        self.assertEqual(spare["scenario_spare_capacity_tonnes"], 350)

    def test_capacity_sweep_preserves_physical_balance_and_effect_bound(self):
        previous_gap = math.inf
        for delta in range(0, 401, 7):
            row = capacity_balance(demand_tonnes=200, capacity_tonnes=100, capacity_delta_tonnes=delta)
            self.assertLessEqual(row["scenario_shortfall_tonnes"], previous_gap)
            self.assertLessEqual(row["shortfall_reduction_tonnes"], min(100, delta))
            self.assertEqual(row["scenario_capacity_tonnes"] + row["scenario_shortfall_tonnes"]
                             - row["scenario_spare_capacity_tonnes"], 200)
            previous_gap = row["scenario_shortfall_tonnes"]

    def test_invalid_and_overflowing_values_are_rejected(self):
        for invalid in (-1, math.inf, -math.inf, math.nan, None, "", True):
            for field in ("demand_tonnes", "capacity_tonnes", "capacity_delta_tonnes"):
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    args = dict(demand_tonnes=100, capacity_tonnes=90, capacity_delta_tonnes=20)
                    args[field] = invalid
                    capacity_balance(**args)
        with self.assertRaises(ValueError):
            capacity_balance(demand_tonnes=100, capacity_tonnes=1e308, capacity_delta_tonnes=1e308)

    def test_api_preserves_status_unit_provenance_and_rejects_overflow(self):
        inputs = dict(demand_tonnes=140, capacity_tonnes=120, capacity_delta_tonnes=30,
                      period="2027", assumption_source="Синтетическое условие теста")
        settings = Settings(_env_file=None, environment="test", api_prefix="/scenario-test/v1",
                            cors_origins=["http://localhost:5173"])
        endpoint = "/scenario-test/v1/scenarios/capacity"
        with TestClient(create_app(settings)) as client:
            http_response = client.post(endpoint, json=inputs)
            self.assertEqual(http_response.status_code, 200)
            response = http_response.json()
            self.assertEqual(response["status"], "scenario")
            self.assertEqual(response["unit"], "tonnes")
            self.assertEqual(response["period"], "2027")
            self.assertEqual(response["assumption_source"], inputs["assumption_source"])
            self.assertEqual(response["scenario_spare_capacity_tonnes"], 10)
            expected = capacity_balance(demand_tonnes=140, capacity_tonnes=120, capacity_delta_tonnes=30)
            self.assertEqual({key: response[key] for key in expected}, expected)
            json.dumps(response, allow_nan=False)
            invalid_inputs = [
                {**inputs, "demand_tonnes": value} for value in ("NaN", "Infinity", -1)
            ] + [
                {**inputs, "capacity_tonnes": 1e308, "capacity_delta_tonnes": 1e308},
                {**inputs, "period": "2027-13"},
            ]
            for invalid in invalid_inputs:
                with self.subTest(inputs=invalid):
                    error_response = client.post(endpoint, json=invalid)
                    self.assertEqual(error_response.status_code, 422)
                    error = error_response.json()["error"]
                    self.assertEqual(error["code"], "VALIDATION_ERROR")
                    self.assertEqual(error["message"], "Некорректные параметры запроса")
                    self.assertTrue(error["details"])
                    self.assertTrue(all(set(item) == {"field", "message"} for item in error["details"]))

    def test_percentage_grid_has_unique_combinations_and_correct_thresholds(self):
        rows = [dict(zip(GRID_FIELDS, r)) for r in make_grid(1_142_984.2654)]
        self.assertEqual(len(rows), 1001)
        keys = {(r["demand_pct"], r["capacity_pct"], r["capacity_delta_pct"]) for r in rows}
        self.assertEqual(len(keys), len(rows))
        for row in rows:
            covered = row["demand_pct"] <= row["capacity_pct"] + row["capacity_delta_pct"]
            self.assertEqual(row["scenario_shortfall_tonnes"] == 0, covered)
            self.assertAlmostEqual(row["capacity_delta_tonnes"] / row["forecast_reference_tonnes"] * 100,
                                   row["capacity_delta_pct"], places=10)


def write_model_fixture(root):
    model = root / "models/synthetic/model.json"
    directions = {}
    for direction in ("inbound", "outbound"):
        values = [float(v) for v in range(100, 109)]
        directions[direction] = {"state": fit_model("naive", values), "years": list(range(2015, 2024)),
                                 "values": values, "origin": "ust_luga" if direction == "inbound" else "kaliningrad_port",
                                 "destination": "kaliningrad_port" if direction == "inbound" else "ust_luga",
                                 "cargo_group": "Синтетика", "source_ids": ["synthetic"],
                                 "source_locators": ["test:1"]}
    artifact = {"schema_version": "0.1", "model_version": "synthetic", "data_version": "synthetic",
                "generated_at": "2026-11-08T00:00:00+00:00", "forecast_origin": 2023,
                "fingerprint": {"end_year": 2024}, "directions": directions,
                "config": {"target": {"route_id": "ust_luga_kaliningrad_ferry", "geography": "Тестовая линия"}},
                "metrics": {d: {"naive": {"holdout": {"MAE": 5, "WAPE": 10, "observations": 2}}} for d in directions},
                "provenance": {"sources": [{"source_id": "synthetic</script><script>test",
                                           "url": "https://example.test/"}]}, "warnings": ["Синтетический тест"]}
    write_json(model, artifact)
    sha = digest(model.read_bytes())
    model.with_suffix(".json.sha256").write_text(sha + "\n", encoding="utf-8")
    write_json(root / "models/latest.json", {"model_path": "models/synthetic/model.json",
               "sha256": sha, "model_version": "synthetic"})
    return model


class SensitivityReportTests(unittest.TestCase):
    def test_offline_report_is_reproducible_keeps_model_unchanged_and_labels_scenarios(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            model = write_model_fixture(root)
            before = model.read_bytes(), (root / "models/latest.json").read_bytes()
            first = generate_report(root)
            output = first["output_dir"]
            hashes = {p.name: digest(p.read_bytes()) for p in output.iterdir()}
            second = generate_report(root)
            self.assertEqual(first["report_version"], second["report_version"])
            self.assertEqual(hashes, {p.name: digest(p.read_bytes()) for p in output.iterdir()})
            self.assertEqual(before, (model.read_bytes(), (root / "models/latest.json").read_bytes()))
            with (output / "sensitivity.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2002)
            self.assertEqual({r["status"] for r in rows}, {"scenario"})
            self.assertEqual({r["period"] for r in rows}, {"2024"})
            self.assertEqual({r["direction"] for r in rows}, {"inbound", "outbound"})
            payload = json.loads((output / "sensitivity.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["defaults"]["period"], "2024")
            self.assertEqual(payload["forecasts"]["inbound"]["status"], "forecast")
            self.assertEqual(payload["human_methodology_review"], "pending")
            text = (output / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("__REPORT_DATA__", text)
            self.assertNotIn("synthetic</script><script>test", text)
            self.assertNotIn("fetch(", text)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            for name, sha in manifest["outputs"].items():
                self.assertEqual(digest((output / name).read_bytes()), sha)

    def test_tampered_model_does_not_produce_report(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            model = write_model_fixture(root)
            model.write_bytes(model.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                generate_report(root)
            self.assertFalse((root / "reports").exists())


if __name__ == "__main__":
    unittest.main()
