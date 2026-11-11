"""Синтетические артефакты: экспорт метрик не раскрывает исходные ряды."""
import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ml.blind_binding import naive_selection_state, selection_lock
from ml.experiment_io import finish_manifest
from ml.export_jury import baseline_verdict, export_jury
from pipeline.common import digest, write_csv, write_json
from pipeline.port_protocol import FIELDS, TARGETS


def fixture(root, *, legacy=False):
    def save(pointer, model, folder):
        path = root / "models" / folder / "model.json"
        write_json(path, model)
        result = {"model_path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes()),
                  "model_version": model["model_version"]}
        if "target_id" in model:
            result["report_path"] = "reports/generated/ml-port/" + model["model_version"]
        write_json(root / pointer, result)
        return result
    m = {"MAE": 10.0, "WAPE": 5.0, "observations": 6, "failed_folds": 0}
    port_metric = {stage: {h: m for h in ("1", "3")} for stage in ("development", "holdout")}
    for target, (port, cargo) in TARGETS.items():
        training = [{**dict.fromkeys(FIELDS, ""), "period": "2025-" + str(i + 1).zfill(2),
                     "target_id": target, "data_version": "synthetic", "value_tonnes": "100"} for i in range(12)]
        naive = naive_selection_state([100.0] * 12)
        state = {**naive, "model_id": "ses"}
        protocol = {"protocol_id": "synthetic"}
        fingerprint = {"data_version": "synthetic", "target_id": target, "protocol": protocol,
                       "dataset_sha256": digest(json.dumps(training, ensure_ascii=False, sort_keys=True).encode())}
        model = {"target_id": target, "port": port, "cargo_group": cargo, "selected_model": "ses",
                 "model_version": "synthetic-" + target, "data_version": "synthetic", "training_start": "2022-06",
                 "observations": 43, "metrics": {"ses": port_metric, "naive": port_metric},
                 "private_training": "PRIVATE_SERIES_MUST_NOT_LEAK", "predictions": [{"value": 918273645}],
                 "state": state, "protocol": protocol, "fingerprint": fingerprint,
                 "selection_lock": selection_lock(target, state, naive, training, protocol)}
        pointer = save(f"models/port/{target}/latest.json", model, target)
        comparisons = [{"period": period, "method_role": role, "model_id": "ses" if role == "selected" else "naive",
                        "absolute_error_tonnes": 10 if role == "selected" else 11, "absolute_percentage_error": 1.0,
                        "actual_status": "observed" if period.endswith("08") else "estimated",
                        "actual_tonnes": 918273645, "predicted_tonnes": 918273646}
                       for period in ("2026-01..2026-08", "2026-09") for role in ("selected", "naive")]
        blind = {"status": "completed", "target_id": target, "selection_changed": False,
                 "selection_lock": model["selection_lock"], "comparisons": comparisons}
        if not legacy:
            blind.update({"model_version": model["model_version"], "data_version": model["data_version"],
                          "model_sha256": pointer["sha256"]})
        write_json(root / "reports/generated/ml-port/blind_checks" / (target + ".json"), blind)
        report = root / pointer["report_path"]
        write_csv(report / "training_series.csv", FIELDS, training)
        write_json(report / "blind_check.json", blind)
        finish_manifest(report, {"model_version": model["model_version"], "data_version": model["data_version"],
                                 "model_sha256": pointer["sha256"], "fingerprint": fingerprint})
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
    def test_legacy_export_requires_frozen_run_and_does_not_reopen_or_rewrite_inputs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root, legacy=True)
            originals = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with patch("pipeline.port_protocol.load_blind_cumulatives", side_effect=AssertionError("closed")):
                export_jury(root, folder)
            evidence = json.loads((root / "docs/jury/evidence.json").read_text())
            self.assertEqual(evidence["monthly_models"]["kaliningrad_ferries"]["binding_method"], "legacy_frozen_run")
            self.assertTrue(all(path.read_bytes() == content for path, content in originals.items()))
            report = root / "reports/generated/ml-port/synthetic-kaliningrad_ferries"
            (report / "blind_check.json").unlink()
            before = (root / "docs/jury/README.md").read_bytes()
            with self.assertRaisesRegex(ValueError, "замороженного"):
                export_jury(root, folder)
            self.assertEqual((root / "docs/jury/README.md").read_bytes(), before)

    def test_changed_model_identity_and_selection_cannot_relabel_old_errors(self):
        for key, value in (("model_version", "new-model"), ("data_version", "new-data"),
                           ("selected_model", "naive"), ("target_id", "other-target")):
            with self.subTest(key=key), TemporaryDirectory() as temp:
                root = Path(temp)
                folder = fixture(root)
                path = root / "models/kaliningrad_ferries/model.json"
                model = json.loads(path.read_text())
                model[key] = value
                write_json(path, model)
                pointer_path = root / "models/port/kaliningrad_ferries/latest.json"
                pointer = json.loads(pointer_path.read_text())
                pointer["sha256"] = digest(path.read_bytes())
                pointer["model_version"] = model["model_version"]
                write_json(pointer_path, pointer)
                manifest_path = root / pointer["report_path"] / "run_manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest.update({"model_sha256": pointer["sha256"], "model_version": model["model_version"]})
                write_json(manifest_path, manifest)
                with self.assertRaisesRegex(ValueError, "не соответствует"):
                    export_jury(root, folder)
                self.assertFalse((root / "docs/jury").exists())

    def test_new_schema_changed_errors_require_matching_frozen_check_and_hash(self):
        for alter_frozen in (False, True):
            with self.subTest(alter_frozen=alter_frozen), TemporaryDirectory() as temp:
                root = Path(temp)
                folder = fixture(root)
                path = root / "reports/generated/ml-port/blind_checks/kaliningrad_ferries.json"
                blind = json.loads(path.read_text())
                blind["comparisons"][0]["absolute_error_tonnes"] = 12345
                write_json(path, blind)
                if alter_frozen:
                    write_json(root / "reports/generated/ml-port/synthetic-kaliningrad_ferries/blind_check.json", blind)
                with self.assertRaisesRegex(ValueError, "SHA-256 замороженной проверки"):
                    export_jury(root, folder)
                self.assertFalse((root / "docs/jury").exists())

    def test_legacy_top_level_data_version_cannot_override_training_version(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root, legacy=True)
            path = root / "models/kaliningrad_ferries/model.json"
            model = json.loads(path.read_text())
            model["data_version"] = "new-data-same-numbers"
            model["fingerprint"]["data_version"] = model["data_version"]
            write_json(path, model)
            pointer_path = root / "models/port/kaliningrad_ferries/latest.json"
            pointer = json.loads(pointer_path.read_text())
            pointer["sha256"] = digest(path.read_bytes())
            write_json(pointer_path, pointer)
            manifest_path = root / pointer["report_path"] / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.update({"model_sha256": pointer["sha256"], "data_version": model["data_version"],
                             "fingerprint": model["fingerprint"]})
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "обучающие данные"):
                export_jury(root, folder)
            self.assertFalse((root / "docs/jury").exists())

    def test_summary_uses_current_metrics_scenarios_ties_and_missing_values(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = fixture(root)
            for target in TARGETS:
                path = root / "reports/generated/ml-port/blind_checks" / (target + ".json")
                blind = json.loads(path.read_text())
                for row in blind["comparisons"]:
                    row["absolute_error_tonnes"] = 10
                write_json(path, blind)
                report = root / "reports/generated/ml-port" / ("synthetic-" + target)
                write_json(report / "blind_check.json", blind)
                manifest = json.loads((report / "run_manifest.json").read_text())
                manifest["outputs"]["blind_check.json"] = digest((report / "blind_check.json").read_bytes())
                write_json(report / "run_manifest.json", manifest)
            path = folder / "action_scenarios.csv"
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                row.update({"capacity_rail_limited_tonnes": 80, "shortfall_rail_limited_tonnes": 70,
                            "reserve_gain_share": "" if row["action"] == "both" else "0.125"})
                if row["action"] == "both":
                    row["shortfall_action_tonnes"] = ""
            write_csv(path, list(rows[0]), rows)
            manifest = json.loads((folder / "run_manifest.json").read_text())
            manifest["outputs"]["action_scenarios.csv"] = digest(path.read_bytes())
            write_json(folder / "run_manifest.json", manifest)
            export_jury(root, folder)
            landing = (root / "docs/jury/README.md").read_text()
            self.assertIn("1,00%", landing)
            self.assertIn("WAPE 5,00%", landing)
            self.assertIn("равны naive", landing)
            self.assertNotIn("3,7%", landing)
            self.assertNotIn("24%", landing)
            scenarios = (root / "docs/jury/scenarios.md").read_text()
            self.assertIn("12,50", scenarios)
            self.assertIn("ограничивает", scenarios)
            self.assertIn("| А + портовый оборот | — | — |", scenarios)
            self.assertNotIn("112 тыс.", scenarios)
            self.assertNotIn("3,2%", scenarios)

    def test_baseline_verdict_handles_missing_equal_worse_and_mixed_errors(self):
        def pairs(errors):
            return {str(i): {"selected": {"absolute_error_tonnes": selected},
                             "naive": {"absolute_error_tonnes": naive}}
                    for i, (selected, naive) in enumerate(errors)}
        self.assertIn("не завершено", baseline_verdict(pairs([(None, 10), (9, 10)])))
        self.assertIn("равны", baseline_verdict(pairs([(10, 10), (10, 10)])))
        self.assertIn("больше", baseline_verdict(pairs([(11, 10), (11, 10)])))
        self.assertIn("смешанный", baseline_verdict(pairs([(9, 10), (11, 10)])))

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
