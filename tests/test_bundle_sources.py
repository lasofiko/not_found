"""Проверки передачи и извлечения; все числовые фикстуры синтетические."""
import csv
import gzip
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from pipeline.bundle_sources import regional_cabotage, september_check, snapshot_parts
from pipeline.common import digest, write_csv, write_json
from pipeline.port_protocol import TARGETS, coverage_row, derive_versions, load_protocol_series, series_asof
from pipeline.supplemental import MONTHLY_FIELDS, PORT_FIELDS, parse_port
from test_port_protocol import cumulative_fixture
from test_supplemental import port_fixture
from test_team_bundle import INDEX, bundle
from test_pipeline import make_registry


class DirectoryTests(unittest.TestCase):
    def fixture(self, root, *, missing=False):
        first, second = root / "part1", root / "part2"
        first.mkdir()
        second.mkdir()
        table = b"period,value\n2025,7\n"
        raw = b"synthetic original"
        payloads = {"data/processed/test.csv": table,
                    "data/processed/build_manifest.json": json.dumps({"outputs": {
                        "data/processed/test.csv": digest(table)}}).encode(),
                    "data/raw/test/original.txt": raw}
        archive = root / "input.zip"
        bundle(archive, payloads)
        with ZipFile(archive) as source:
            for name in source.namelist():
                base = second if name.startswith("data/raw/") else first
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source.read(name))
        if missing:
            (second / "data/raw/test/original.txt").unlink()
        return first, second

    def test_snapshot_is_repeatable_and_records_missing_optional_original(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = self.fixture(root, missing=True)
            target = root / "work"
            one = snapshot_parts(target, first, second)
            before = one.read_bytes()
            # Повторное создание, а не только использование кэша снимка.
            one.unlink()
            two = snapshot_parts(target, first, second)
            self.assertEqual(before, two.read_bytes())
            receipt = json.loads(two.with_name(two.stem + "-receipt.json").read_text())
            self.assertEqual(receipt["kind"], "directory_snapshot")
            self.assertEqual(receipt["missing_files"], ["data/raw/test/original.txt"])

    def test_corrupt_raw_or_missing_required_table_prevents_any_install(self):
        for failure in ("raw", "table", "misplaced_table"):
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                first, second = self.fixture(root)
                if failure == "raw":
                    (second / "data/raw/test/original.txt").write_bytes(b"changed")
                elif failure == "table":
                    (first / "data/processed/test.csv").unlink()
                else:
                    misplaced = second / "data/processed/test.csv"
                    misplaced.parent.mkdir(parents=True)
                    (first / "data/processed/test.csv").replace(misplaced)
                with self.assertRaises(ValueError):
                    snapshot_parts(root / "work", first, second)
                self.assertFalse((root / "work").exists())

    def test_symlink_and_parent_escape_are_rejected(self):
        for failure in ("symlink", "path"):
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                first, second = self.fixture(root)
                if failure == "symlink":
                    path = first / "data/processed/test.csv"
                    payload = path.read_bytes()
                    path.unlink()
                    outside = root / "outside.csv"
                    outside.write_bytes(payload)
                    path.symlink_to(outside)
                else:
                    index = json.loads((first / INDEX).read_text())
                    index["files"][0]["path"] = "../outside.csv"
                    (first / INDEX).write_text(json.dumps(index))
                with self.assertRaises(ValueError):
                    snapshot_parts(root / "work", first, second)


class ExtractionTests(unittest.TestCase):
    def test_loader_reports_target_cell_validation_separately_from_base(self):
        raw, artifacts = cumulative_fixture()
        for row in raw:
            row.update(port=TARGETS["petropavlovsk_total"][0], cargo_group="Всего грузов")
        manifest = {"data_version": "synthetic", "raw_port_enrichment": True,
                    "verification_scope": "provided_tables", "original_source_validation": "not_performed"}
        with patch("pipeline.verify.verify", return_value=[]), patch(
                "pipeline.port_protocol.read_history", return_value=(raw, manifest, artifacts)):
            rows, provenance = load_protocol_series(Path("."), "petropavlovsk_total")
        self.assertEqual(len(rows), 43)
        self.assertEqual(provenance["verification_scope"], "reextracted_port_cells")
        self.assertEqual(provenance["base_verification_scope"], "provided_tables")
        self.assertEqual(provenance["human_methodology_review"], "pending")

    def test_two_ports_keep_total_scope_and_skip_future_cell(self):
        for port in ("Порт Петропавловск-Камчатский", "Порт Владивосток"):
            book = port_fixture()
            book[0][1][4]["A"] = port
            book[0][1][8]["A"] = "Итого " + port
            rows = parse_port(book, {"source_id": "test", "publication_year": 2025,
                                    "publication_month": 1}, "synthetic",
                              target_ports={port}, cargo_groups={"Всего грузов"}, max_year=2025)
            totals = [r for r in rows if r["measurement_scope"] == "port_total"]
            self.assertEqual(len(totals), 2)
            self.assertTrue(all(r["port"] == port for r in totals))
            self.assertEqual({r["source_locator"] for r in totals}, {"synthetic_sheet!C8", "synthetic_sheet!C9"})
        book = port_fixture()
        book[0][1][1]["A"] = "Объём перевалки за январь 2026 г."
        for row in book[0][1].values():
            if row.get("B") == "25":
                row.update(B="26", C="future_value_must_not_be_parsed")
            elif row.get("B") == "24":
                row["B"] = "25"
        rows = parse_port(book, {"source_id": "test", "publication_year": 2026,
                                "publication_month": 1}, "synthetic", max_year=2025)
        self.assertEqual({r["period"] for r in rows}, {"2025-01"})

    def test_coverage_counts_calendar_and_historically_available_months_separately(self):
        raw, artifacts = cumulative_fixture()
        target = "vladivostok_total"
        for row in raw:
            row.update(port=TARGETS[target][0], cargo_group="Всего грузов")
        versions, _ = derive_versions(raw, artifacts, "synthetic", target)
        rows = series_asof(versions, target)
        coverage = coverage_row(target, rows)
        self.assertEqual(coverage["consecutive_before_selection"], 25)
        self.assertEqual(coverage["consecutive_available_before_selection"], 24)
        self.assertTrue(coverage["model_selection_eligible"])
        for row in rows:
            if row["period"] == "2023-01":
                row["value_tonnes"] = ""
        self.assertFalse(coverage_row(target, rows)["model_selection_eligible"])

    def test_september_result_is_redacted_and_detects_changed_column(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts, books, provided = [], {}, []
            for month, value in ((8, "112233"), (9, "223344")):
                artifact = {"source_id": "synthetic_" + str(month), "sha256": "f" * 64,
                            "dataset_kind": "port_cabotage", "publication_year": 2026, "publication_month": month}
                book = port_fixture(month, total=value)
                book[0][1][1]["A"] = f"Объём перевалки за {month} месяцев 2026 г."
                for row in book[0][1].values():
                    if row.get("B") == "25":
                        row["B"] = "26"
                    elif row.get("B") == "24":
                        row["B"] = "25"
                artifacts.append(artifact)
                books[artifact["source_id"]] = book
                provided.extend(r for r in parse_port(book, artifact, "synthetic")
                                if r["period"].startswith("2026") and r["measurement_scope"] == "port_total")
            write_json(root / "data/source_registry.json", {"schema_version": "0.2", "artifacts": artifacts})
            write_csv(root / "data/processed/port_cabotage_cumulative.csv", PORT_FIELDS, provided)
            monthly = {**provided[-1], "value": "111111000", "previous_source_id": "synthetic_8", "formula": "synthetic"}
            write_csv(root / "data/processed/port_cabotage_monthly.csv", MONTHLY_FIELDS, [monthly])
            with patch("pipeline.bundle_sources.sheets", side_effect=lambda _, a: books[a["source_id"]]):
                result = september_check(root)
                self.assertEqual(result["result"], "разность верна")
                self.assertFalse(result["blind_check_run"])
                self.assertNotIn("112233", json.dumps(result))
                self.assertNotIn("223344", json.dumps(result))
                provided[0]["value"] = "0"
                write_csv(root / "data/processed/port_cabotage_cumulative.csv", PORT_FIELDS, provided)
                self.assertIn("ошибка", september_check(root)["result"])

    def test_regional_cabotage_preserves_accumulation_and_never_emits_future(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "data/raw/freight_packages/example/nsedc_port_39233_data.csv.gz"
            path.parent.mkdir(parents=True)
            row = {"Виды грузов": "57 всего грузов", "Виды плавания": "70 каботаж",
                   "Единица измерения": "169 тысяча тонн", "отправлено-прибыло": "2 прибыло",
                   "Период": "120 январь-июнь", "Субъекты РФ": "11270000 Калининградская область",
                   "2025": "1.2", "2026": "must_not_be_parsed"}
            with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            result, sources = regional_cabotage(root, "synthetic")
            last = next(r for r in result if r["period"] == "2025-06")
            self.assertEqual(last["value_tonnes"], "1200.0")
            self.assertEqual(last["period_basis"], "year_to_date")
            self.assertEqual(last["direction"], "")
            self.assertTrue(all(r["period"] < "2026" for r in result))
            self.assertEqual(sources[0]["sha256"], digest(path.read_bytes()))


class EnrichedManifestTests(unittest.TestCase):
    def test_generated_tampering_is_detected_even_with_replaced_output_hash(self):
        from pipeline.build import build
        from pipeline.common import ROOT
        from pipeline.reproduce import reproduce
        from pipeline.team_bundle import rebuild_processed
        from pipeline.verify import verify
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            originals = root / "data/raw/not_found_additions"
            originals.mkdir(parents=True)
            for name in ("one.txt", "two.txt"):
                (originals / name).write_bytes(b"same synthetic original")
            (root / "ml").mkdir()
            shutil.copyfile(ROOT / "ml/port_protocol.toml", root / "ml/port_protocol.toml")
            original = build(root)
            relative = "data/processed/port_cabotage_cumulative.csv"
            write_csv(root / relative, PORT_FIELDS, [])
            original["outputs"][relative] = digest((root / relative).read_bytes())
            original["table_rows"]["port_cabotage_cumulative.csv"] = 0
            write_json(root / "data/processed/build_manifest.json", original)
            payloads = {p: (root / p).read_bytes() for p in original["outputs"] if p.startswith("data/processed/")}
            payloads["data/processed/build_manifest.json"] = (root / "data/processed/build_manifest.json").read_bytes()
            archive = root / "provided.zip"
            bundle(archive, payloads)
            rebuild_processed(root, archive, enrich=True)
            self.assertEqual(verify(root), [])
            with (root / "data/processed/team_bundle/original_source_registry.csv").open(encoding="utf-8-sig", newline="") as f:
                sources = list(csv.DictReader(f))
            self.assertEqual(len(sources), 1)
            self.assertEqual(len(json.loads(sources[0]["acquisition_locations"])), 2)
            self.assertEqual(reproduce(root)["result"], "identical")
            manifest_path = root / "data/processed/build_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            relative = "data/processed/port_protocol/coverage.csv"
            path = root / relative
            path.write_bytes(path.read_bytes().replace(b",False", b",True"))
            manifest["generated_outputs"][relative] = digest(path.read_bytes())
            write_json(manifest_path, manifest)
            self.assertTrue(any("Покрытие" in error for error in verify(root)))
