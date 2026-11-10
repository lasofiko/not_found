"""Синтетические артефакты: экспорт метрик не раскрывает исходные ряды."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ml.export_jury import export_jury
from pipeline.common import digest, write_csv, write_json
from pipeline.port_protocol import TARGETS


def fixture(root):
    def save(pointer, model, folder):
        path = root / "models" / folder / "model.json"
        write_json(path, model)
        write_json(root / pointer, {"model_path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes())})
    m = {"MAE": 10.0, "WAPE": 5.0, "observations": 6, "failed_folds": 0}
    port_metric = {stage: {h: m for h in ("1", "3")} for stage in ("development", "holdout")}
    for target, (port, cargo) in TARGETS.items():
        model = {"target_id": target, "port": port, "cargo_group": cargo, "selected_model": "ses",
                 "model_version": "synthetic-" + target, "data_version": "synthetic", "training_start": "2022-06",
                 "observations": 43, "metrics": {"ses": port_metric, "naive": port_metric},
                 "private_training": "PRIVATE_SERIES_MUST_NOT_LEAK", "predictions": [{"value": 918273645}]}
        save(f"models/port/{target}/latest.json", model, target)
        comparisons = [{"period": period, "method_role": role, "model_id": "ses" if role == "selected" else "naive",
                        "absolute_error_tonnes": 10 if role == "selected" else 11, "absolute_percentage_error": 1.0,
                        "actual_status": "observed" if period.endswith("08") else "estimated",
                        "actual_tonnes": 918273645, "predicted_tonnes": 918273646}
                       for period in ("2026-01..2026-08", "2026-09") for role in ("selected", "naive")]
        write_json(root / "reports/generated/ml-port/blind_checks" / (target + ".json"), {
            "status": "completed", "selection_changed": False, "selection_lock": "synthetic-lock", "comparisons": comparisons})
    years = list(range(2015, 2024))
    annual = {"model_version": "synthetic-annual", "data_version": "synthetic",
              "directions": {d: {"state": {"model_id": "naive"}, "years": years, "values": [123.0] * 9}
                             for d in ("inbound", "outbound")},
              "metrics": {d: {"naive": {stage: m for stage in ("development", "holdout")}}
                          for d in ("inbound", "outbound")}}
    save("models/latest.json", annual, "annual")
    write_csv(root / "data/processed/candidate_observations.csv", ["direction", "period", "value_tonnes"],
              [{"direction": d, "period": y, "value_tonnes": "123"} for d in ("inbound", "outbound") for y in years])
    write_json(root / "data/review-status.json", {"status": "partial", "reviewer": "Петя", "reviewed_cells": None})
    folder = root / "reports/generated/ml-hypotheses/synthetic"
    write_csv(folder / "action_scenarios.csv", ["year", "direction", "demand_case", "action",
              "shortfall_action_tonnes", "capacity_action_tonnes", "capacity_rail_limited_tonnes"],
              [{"year": "2027", "direction": "inbound", "demand_case": "demand_x1_5", "action": action,
                "shortfall_action_tonnes": deficit, "capacity_action_tonnes": 100, "capacity_rail_limited_tonnes": 100}
               for action, deficit in (("none", 50), ("reserve_ferry", 40), ("port_turnaround", 30), ("both", 20))])
    write_csv(folder / "hypothesis_results.csv", ["indicator", "central"], [
        {"indicator": "Потенциал на ремонт «Амбала», 73 дня", "central": 67000},
        {"indicator": "Потенциал на все ремонты 2026", "central": 112000},
        {"indicator": "Запас ж/д части до предела по грузу в вагонах", "central": 630000}])
    write_json(folder / "run_manifest.json", {"data_version": "synthetic", "outputs": {
        name: digest((folder / name).read_bytes()) for name in ("action_scenarios.csv", "hypothesis_results.csv")}})
    return folder


class JuryExportTests(unittest.TestCase):
    def test_changed_scenario_result_cannot_be_exported_as_verified(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root)
            path = folder / "action_scenarios.csv"
            path.write_bytes(path.read_bytes().replace(b"100", b"101", 1))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export_jury(root, folder)
            self.assertFalse((root / "docs/jury").exists())

    def test_export_contains_metrics_and_partial_review_but_no_training_or_predictions(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root)
            originals = {p: digest(p.read_bytes()) for p in root.rglob("*.json")}
            result = export_jury(root, folder)
            self.assertEqual(result["cards"], 6)
            self.assertEqual(result["metric_rows"], 44)
            text = "\n".join(p.read_text(encoding="utf-8-sig") for p in (root / "docs/jury").iterdir())
            self.assertIn("частично", text)
            self.assertNotIn("PRIVATE_SERIES_MUST_NOT_LEAK", text)
            self.assertNotIn("918273645", text)
            self.assertNotIn('"predicted_tonnes"', text)
            self.assertTrue(all(digest(p.read_bytes()) == sha for p, sha in originals.items()))

    def test_corrupt_model_stops_export_before_writing_documents(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root)
            pointer = json.loads((root / "models/port/kaliningrad_ferries/latest.json").read_text())
            (root / pointer["model_path"]).write_text("{}")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export_jury(root, folder)
            self.assertFalse((root / "docs/jury").exists())

    def test_changed_annual_observation_cannot_be_hidden_by_new_document_version(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root)
            path = root / "data/processed/candidate_observations.csv"
            path.write_bytes(path.read_bytes().replace(b"123", b"124", 1))
            with self.assertRaisesRegex(ValueError, "других наблюдениях"):
                export_jury(root, folder)
            self.assertFalse((root / "docs/jury").exists())
