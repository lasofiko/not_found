"""Проверки передачи на синтетических источниках; человеческая приёмка не имитируется."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pipeline.build import build
from pipeline.common import ROOT, digest, write_csv
from pipeline.input_check import INPUT_FIELDS, check_input
from pipeline.package import package
from pipeline.target_audit import REVIEW_FIELDS, check_source, human_review
from pipeline.verify import verify
from test_pipeline import test_registry


class TargetHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.artifacts = test_registry(self.root)
        for relative in ("docs/contracts.md", "docs/data-preparation.md", "docs/data-input-guide.md",
                         "data/README.md", "data/examples/observations-template.csv", "data/human-review-log.csv"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Реальные отметки проверки не переносим в тестовую фикстуру.
            if relative == "data/human-review-log.csv":
                write_csv(target, REVIEW_FIELDS, [])
            else:
                shutil.copyfile(ROOT / relative, target)
        self.manifest = build(self.root)
        with (self.root / "data/processed/candidate_observations.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.rows = list(csv.DictReader(stream))

    def test_every_cell_has_exact_source_position_and_empty_human_log(self):
        checks, _ = check_source(self.root, self.rows, self.artifacts)
        self.assertEqual(len(checks), 18)
        self.assertEqual({r["html_row_index"] for r in checks}, {5, 7})
        self.assertEqual({r["html_value_cell_index"] for r in checks}, {2})
        self.assertEqual({r["published_label"] for r in checks}, {"Выгружено (тонн)", "Погружено (тонн)"})
        self.assertEqual(human_review(self.root, checks)["status"], "pending")
        self.assertFalse((self.root / "data/processed/observations.csv").exists())

    def test_matching_numeric_value_with_wrong_direction_or_locator_is_rejected(self):
        for change in ({"value_tonnes": "124"}, {"original_unit": "тыс. тонн"},
                       {"source_locator": "#2023y / ЖДПК / ВСЕГО (тонн)"},
                       {"direction": "outbound"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_source(self.root, [{**self.rows[0], **change}], self.artifacts)

    def test_missing_matrix_does_not_create_mass_or_months(self):
        with (self.root / "data/processed/corridor-coverage.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 96)
        self.assertEqual(sum(r["coverage_status"] == "not_confirmed" for r in rows), 78)
        self.assertTrue(all(r["coverage_status"] == "not_confirmed" for r in rows if r["period"] >= "2024"))
        self.assertEqual({r["period_granularity"] for r in self.rows}, {"year"})

    def test_review_is_hash_bound_versioned_and_never_approves_target(self):
        checks, _ = check_source(self.root, self.rows, self.artifacts)
        item = checks[0]
        record = {"reviewer": "Синтетическая тестовая запись", "reviewed_at": "2026-11-09",
                  "source_id": item["source_id"], "source_sha256": item["source_sha256"],
                  "source_locator": item["source_locator"], "result": "unresolved", "comment": "Только фикстура"}
        path = self.root / "data/human-review-log.csv"
        write_csv(path, REVIEW_FIELDS, [record])
        self.assertTrue(any("human-review-log" in error for error in verify(self.root)))
        manifest = build(self.root)
        self.assertNotEqual(self.manifest["data_version"], manifest["data_version"])
        self.assertEqual(manifest["target_selection"], "pending")
        self.assertEqual(human_review(self.root, checks)["issues"], 1)
        self.assertEqual(verify(self.root), [])
        write_csv(path, REVIEW_FIELDS, [{**record, "source_sha256": "0" * 64}])
        with self.assertRaises(ValueError):
            build(self.root)

    def test_complete_recorded_review_still_does_not_publish_final_observations(self):
        checks, _ = check_source(self.root, self.rows, self.artifacts)
        records = [{"reviewer": "Синтетическая тестовая запись", "reviewed_at": "2026-11-09",
                    "source_id": c["source_id"], "source_sha256": c["source_sha256"],
                    "source_locator": c["source_locator"], "result": "matched", "comment": "Только фикстура"} for c in checks]
        write_csv(self.root / "data/human-review-log.csv", REVIEW_FIELDS, records)
        manifest = build(self.root)
        self.assertEqual(manifest["target_selection"], "pending")
        self.assertFalse((self.root / "data/processed/observations.csv").exists())
        self.assertEqual(human_review(self.root, checks)["status"], "recorded_complete")

    def test_deterministic_archive_includes_assets_and_rebuilds_in_new_interpreter(self):
        output = Path(self.temp.name) / "data-handoff.zip"
        package(self.root, output)
        before = digest(output.read_bytes())
        package(self.root, output)
        self.assertEqual(before, digest(output.read_bytes()))
        destination = Path(self.temp.name) / "recipient"
        with ZipFile(output) as archive:
            archive.extractall(destination)
        for relative in ("data/human-review-log.csv", "data/examples/observations-template.csv", "docs/target-series-passport.md"):
            self.assertTrue((destination / relative).is_file())
        result = subprocess.run([sys.executable, "-m", "pipeline.reproduce"], cwd=destination,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "identical")


class UserInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        artifacts = test_registry(self.root)
        self.path = self.root / "user.csv"
        self.row = dict.fromkeys(INPUT_FIELDS, "")
        self.row.update(period="2023", period_granularity="year", origin="ust_luga", destination="kaliningrad_port",
                        direction="inbound", mode="sea", cargo_group="Все грузы", value_tonnes="1000", status="observed",
                        source_id=artifacts[1]["source_id"], source_locator="#2023y / Паромная линия / Выгружено (тонн)",
                        original_value="1", original_unit="тыс. тонн", conversion_factor="1000")

    def check(self, rows, **options):
        write_csv(self.path, INPUT_FIELDS, rows)
        return check_input(self.path, root=self.root, **options)

    def test_real_zero_missing_and_unit_conversions(self):
        for original, unit, factor, tonnes in [("1", "тыс. тонн", "1000", "1000"),
                ("0,001", "млн тонн", "1000000", "1000"), ("0", "тонн", "1", "0"), ("–", "тонн", "1", "")]:
            self.assertEqual(self.check([{**self.row, "original_value": original, "original_unit": unit,
                                          "conversion_factor": factor, "value_tonnes": tonnes}]), [])
        self.assertTrue(self.check([{**self.row, "original_value": "–", "value_tonnes": "0"}]))

    def test_rejects_counts_unexplained_codes_and_missing_formula(self):
        for change in ({"original_unit": "TEU"}, {"original_value": "ND"}, {"original_value": "-99999999"},
                       {"status": "estimated"}, {"source_locator": ""}, {"status": "forecast"},
                       {"in_wagon_on_ferry": "true", "mode": "rail_land"}):
            with self.subTest(change=change):
                self.assertTrue(self.check([{**self.row, **change}]))
        self.assertEqual(self.check([{**self.row, "status": "estimated", "formula": "Синтетический расчёт 1 × 1000"}]), [])
        self.assertTrue(self.check([{**self.row, "status": "synthetic"}], observed_only=True))

    def test_only_used_originals_are_required_and_tampering_is_rejected(self):
        artifacts = json.loads((self.root / "data/source_registry.json").read_text())["artifacts"]
        (self.root / artifacts[0]["local_path"]).unlink()
        self.assertEqual(self.check([self.row]), [])
        (self.root / artifacts[1]["local_path"]).write_text("corrupt")
        self.assertTrue(self.check([self.row]))

    def test_conflicts_and_double_count_of_shipment_between_modes(self):
        self.assertTrue(self.check([self.row, {**self.row, "source_id": "another"}]))
        rows = [{**self.row, "shipment_id": "shipment-1"},
                {**self.row, "shipment_id": "shipment-1", "mode": "rail_land"}]
        self.assertTrue(any("отправка" in e for e in self.check(rows)))


if __name__ == "__main__":
    unittest.main()
