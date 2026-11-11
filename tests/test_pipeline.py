"""Тестовые числа здесь синтетические и никогда не становятся данными проекта."""
import csv
from io import BytesIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pipeline.acquire import register
from pipeline.build import build
from pipeline.common import OBSERVATION_FIELDS, ROOT, digest, verify_artifact, write_csv, write_json
from pipeline.extract import extract_ferry, extract_freight, mass
from pipeline.package import package
from pipeline.validate import valid_period, validate
from pipeline.verify import verify


def freight_fixture(years=range(2015, 2024)):
    panels = []
    for year in years:
        panels.append(f'''<div id="{year}y"><table><tbody>
        <tr><td colspan="2">Железнодорожный паромный комплекс в морском порту Калининград</td></tr>
        <tr><td>ВСЕГО (тонн)</td><td>999</td></tr>
        <tr><td>Каботаж (тонн)</td><td>900</td></tr>
        <tr><td colspan="2">Паромная линия «Морской порт Усть-Луга – морской порт Калининград»</td></tr>
        <tr><td>Выгружено (тонн)</td><td>123</td></tr>
        <tr><td>Вагоны (ед.)</td><td>9999</td></tr>
        <tr><td>Погружено (тонн)</td><td>45</td></tr>
        </tbody></table></div>''')
    return '''<html><body><p>Производственные мощности ЖДПК позволяют обрабатывать до 5,3 млн тонн грузов в год</p>
    <p>Перевалочные мощности Морского терминала позволяют обрабатывать до 20 тыс. стандартных грузовых фур в год</p>''' + "".join(panels) + "</body></html>"


def ferry_fixture():
    return '''<html><table><tr><td>Год</td><td>Техника</td><td>Вагоны</td><td>Количество перевезенных грузов, тыс. тонн</td><td>Пассажиры</td></tr>
    <tr><td>2022</td><td>900</td><td>800</td><td>1 234,5</td><td>700</td></tr></table>
    <table><tr><td>Год</td><td>Техника</td><td>Количество перевезенных грузов, тыс. тонн</td></tr>
    <tr><td>2025</td><td>–</td><td>–</td></tr></table></html>'''


def catalog_fixture():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheetData><row r="2"><c r="A2" t="inlineStr"><is><t>Тестовый источник</t></is></c><c r="B2" t="inlineStr"><is><t>Только для теста</t></is></c></row></sheetData>
        <hyperlinks><hyperlink ref="A2" r:id="rId1"/></hyperlinks></worksheet>''')
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="https://example.test/source"/></Relationships>''')
    return buffer.getvalue()


def make_registry(root):
    shutil.copytree(ROOT / "pipeline", root / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
    artifacts = []
    for key, payload, fmt in (("hackathon_catalog", catalog_fixture(), "xlsx"),
                               ("rosmorport_freight", freight_fixture().encode(), "html"),
                               ("rosmorport_ferry", ferry_fixture().encode(), "html")):
        spec = {"source_key": key, "format": fmt, "organization": "Синтетический тестовый источник",
                "url": "https://example.test/" + key, "purpose": "reference", "period": None,
                "geography": "Только тест", "direction": None, "mode": "sea", "metric": "Тест",
                "original_unit": "тонн", "limitations": "Не реальные наблюдения"}
        artifacts.append(register(root, spec, payload, spec["url"], "text/html", "utf-8"))
    write_json(root / "data/source_registry.json", {"schema_version": "0.2", "artifacts": artifacts, "failures": []})
    return artifacts


class ExtractionTests(unittest.TestCase):
    def test_russian_decimal_and_nonbreaking_spaces(self):
        self.assertEqual(mass("1\u00a0234,5", "тыс. тонн"), "1234500.0")

    def test_dash_is_missing_and_zero_is_zero(self):
        self.assertEqual(mass("–", "тонн"), "")
        self.assertEqual(mass("0", "тонн"), "0")

    def test_unexplained_codes_and_counts_are_rejected(self):
        for token in ("ND", "CD", "-77777777", "-99999999", "более 100", "NaN"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                mass(token, "тонн")
        with self.assertRaises(ValueError):
            mass("15", "TEU")

    def test_line_cells_are_separated_from_terminal_and_wagons(self):
        line, terminal = extract_freight(freight_fixture([2023]), [2023])
        self.assertEqual([r["value_tonnes"] for r in line], ["123", "45"])
        self.assertEqual([r["value_tonnes"] for r in terminal], ["999", "900"])
        self.assertTrue(all("Паромная линия" in r["source_locator"] for r in line))

    def test_missing_or_duplicate_cell_stops_extraction(self):
        html = freight_fixture([2023])
        for changed in (html.replace("Выгружено (тонн)", "Неизвестно"),
                        html.replace("</tbody>", "<tr><td>Выгружено (тонн)</td><td>50</td></tr></tbody>")):
            with self.assertRaises(ValueError):
                extract_freight(changed, [2023])

    def test_missing_year_stops_extraction(self):
        with self.assertRaises(ValueError):
            extract_freight(freight_fixture([2023]), [2022, 2023])

    def test_ferry_uses_tonnes_column_and_keeps_unknown_direction(self):
        rows = extract_ferry(ferry_fixture())
        self.assertEqual(rows[0]["value_tonnes"], "1234500.0")
        self.assertEqual(rows[1]["value_tonnes"], "")
        self.assertNotIn("direction", rows[0])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "observations.csv"
        self.row = dict(zip(OBSERVATION_FIELDS, ["2023", "year", "ust_luga", "kaliningrad_port", "inbound", "sea", "Все грузы", "123", "observed", "fixture"]))

    def check(self, rows, **options):
        fields = list(dict.fromkeys(OBSERVATION_FIELDS + [k for row in rows for k in row]))
        write_csv(self.path, fields, rows)
        return validate(self.path, **options)

    def test_calendar_rejects_impossible_months_and_year_zero(self):
        for period, step in (("2026-00", "month"), ("2026-13", "month"), ("0000", "year"), ("2026-1", "month")):
            self.assertFalse(valid_period(period, step))
        self.assertTrue(valid_period("2026-02", "month"))

    def test_direction_must_match_corridor(self):
        self.assertEqual(self.check([self.row], corridor=True), [])
        self.assertTrue(self.check([{**self.row, "direction": "outbound"}], corridor=True))
        self.assertTrue(self.check([{**self.row, "origin": "vilnius"}], corridor=True))

    def test_duplicate_from_different_source_is_not_summed(self):
        errors = self.check([self.row, {**self.row, "source_id": "another", "value_tonnes": "124"}])
        self.assertTrue(any("конфликт" in error for error in errors))

    def test_nonfinite_negative_and_synthetic_target_are_rejected(self):
        for value in ("NaN", "Infinity", "-1"):
            self.assertTrue(self.check([{**self.row, "value_tonnes": value}]))
        self.assertTrue(self.check([{**self.row, "status": "synthetic"}], observed_only=True))

    def test_ferry_cannot_be_counted_as_land_rail(self):
        self.assertTrue(self.check([{**self.row, "mode": "rail_land", "in_wagon_on_ferry": "true"}]))

    def test_missing_cannot_become_zero_and_conversion_must_match(self):
        extra = {"original_value": "–", "original_unit": "тонн", "conversion_factor": "1"}
        self.assertTrue(self.check([{**self.row, **extra, "value_tonnes": "0"}]))
        self.assertEqual(self.check([{**self.row, **extra, "value_tonnes": ""}]), [])
        self.assertTrue(self.check([{**self.row, "original_value": "1,23", "original_unit": "тыс. тонн", "conversion_factor": "1000"}]))

    def test_empty_dataset_is_not_ready_for_training(self):
        self.assertTrue(self.check([], require_observations=True))

    def test_ragged_csv_returns_error(self):
        self.path.write_text(",".join(OBSERVATION_FIELDS) + "\n2023,year\n", encoding="utf-8")
        self.assertTrue(validate(self.path))

    def test_source_and_original_are_required_with_registry(self):
        artifacts = make_registry(self.root)
        self.assertTrue(self.check([self.row], registry=self.root / "data/source_registry.json", root=self.root))
        registered = {**self.row, "source_id": artifacts[0]["source_id"]}
        self.assertEqual(self.check([registered], registry=self.root / "data/source_registry.json", root=self.root), [])
        original = self.root / artifacts[0]["local_path"]
        original.write_bytes(b"tampered")
        self.assertTrue(self.check([registered], registry=self.root / "data/source_registry.json", root=self.root))

    def test_original_path_cannot_escape_raw_directory(self):
        with self.assertRaises(ValueError):
            verify_artifact(self.root, {"local_path": "../outside", "source_id": "fixture", "sha256": "x"})


class BuildTests(unittest.TestCase):
    def test_package_can_be_checked_after_extraction(self):
        with TemporaryDirectory() as name:
            root = Path(name) / "source"
            root.mkdir()
            make_registry(root)
            for relative in ("docs/data-preparation.md", "docs/contracts.md", "data/README.md"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            build(root)
            output = package(root, Path(name) / "handoff.zip")
            destination = Path(name) / "recipient"
            with ZipFile(output) as archive:
                archive.extractall(destination)
            self.assertEqual(verify(destination), [])
            self.assertTrue((destination / "HANDOFF.md").is_file())
            self.assertTrue(output.with_suffix(".zip.sha256").read_text().startswith(digest(output.read_bytes())))

    def test_rebuild_is_identical_keeps_annual_granularity_and_does_not_approve_target(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            make_registry(root)
            primary = root / "data/processed/observations.csv"
            primary.parent.mkdir(parents=True)
            primary.write_text("existing team work", encoding="utf-8")
            first = build(root)
            before = {p: (root / p).read_bytes() for p in first["outputs"]}
            second = build(root)
            self.assertEqual(first, second)
            self.assertEqual(before, {p: (root / p).read_bytes() for p in second["outputs"]})
            self.assertEqual(first["candidate_rows"], 18)
            self.assertEqual(first["target_selection"], "pending")
            self.assertEqual(primary.read_text(), "existing team work")
            self.assertEqual(verify(root), [])
            with (root / "data/processed/candidate_observations.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["period_granularity"] for r in rows}, {"year"})
            inbound = next(r for r in rows if r["direction"] == "inbound")
            self.assertEqual(inbound["origin"], "ust_luga")
            self.assertEqual(inbound["value_tonnes"], "123")

    def test_changed_original_stops_build(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            artifacts = make_registry(root)
            (root / artifacts[0]["local_path"]).write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                build(root)

    def test_missing_line_or_cabotage_keeps_reconciliation_incomplete(self):
        for value in ("123", "45", "900"):
            with self.subTest(value=value), TemporaryDirectory() as name:
                root = Path(name)
                artifacts = make_registry(root)
                original = next(a for a in artifacts if a["source_key"] == "rosmorport_freight")
                payload = freight_fixture().replace(f"<td>{value}</td>", "<td>–</td>", 1).encode()
                changed = register(root, original, payload, original["url"], "text/html", "utf-8")
                artifacts = [changed if a == original else a for a in artifacts]
                write_json(root / "data/source_registry.json", {"schema_version": "0.2", "artifacts": artifacts})
                build(root)
                self.assertEqual(verify(root), [])
                with (root / "data/interim/reconciliation.csv").open(encoding="utf-8-sig") as stream:
                    row = next(csv.DictReader(stream))
                self.assertEqual(row["check_status"], "incomplete")
                self.assertEqual(row["difference_tonnes"], "")
                self.assertEqual(row["line_sum_tonnes"], "168" if value == "900" else "")
                self.assertEqual(row["terminal_cabotage_tonnes"], "" if value == "900" else "900")
                log = json.loads((root / "data/processed/transformation_log.json").read_text())
                self.assertEqual(log["reconciliation_incomplete"], 1)
                quality = json.loads((root / "data/processed/target-quality.json").read_text())
                self.assertEqual(quality["line_terminal_incomplete_years"], ["2015"])
                self.assertIn("нет данных", (root / "docs/data-quality-report.md").read_text())

    def test_zero_mass_is_valid_for_reconciliation(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            artifacts = make_registry(root)
            original = next(a for a in artifacts if a["source_key"] == "rosmorport_freight")
            payload = freight_fixture().replace("<td>123</td>", "<td>0</td>", 1).encode()
            changed = register(root, original, payload, original["url"], "text/html", "utf-8")
            write_json(root / "data/source_registry.json", {
                "schema_version": "0.2", "artifacts": [changed if a == original else a for a in artifacts]})
            build(root)
            with (root / "data/interim/reconciliation.csv").open(encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual((row["line_sum_tonnes"], row["difference_tonnes"], row["check_status"]),
                             ("45", "855", "mismatch"))
            self.assertEqual(verify(root), [])

    def test_output_tampering_is_detected(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            make_registry(root)
            build(root)
            path = root / "data/processed/context_observations.csv"
            path.write_bytes(path.read_bytes().replace(b"1234500.0", b"9999999.0"))
            self.assertTrue(verify(root))

    def test_registration_keeps_old_original_when_source_changes(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            first = make_registry(root)[0]
            changed = register(root, first, b"new bytes", first["url"], "text/html", "utf-8")
            self.assertNotEqual(first["source_id"], changed["source_id"])
            self.assertTrue((root / first["local_path"]).is_file())
            self.assertEqual(digest((root / changed["local_path"]).read_bytes()), changed["sha256"])


if __name__ == "__main__":
    unittest.main()
