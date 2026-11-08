"""Синтетические примеры: различия охвата, единиц, версий и режимов передачи."""
import copy
import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
import unicodedata
from zipfile import ZipFile

from pipeline.acquire import register
from pipeline.build import build
from pipeline.common import ROOT, digest, write_json
from pipeline.local_import import BUNDLES, import_bundle, import_directory
from pipeline.package import package
from pipeline.supplemental import regional, parse_archive_csv, parse_port, port_monthly
from pipeline.verify import verify
from test_pipeline import make_registry


class ImportTests(unittest.TestCase):
    def test_unicode_reimport_restores_bytes_without_changing_passport(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            incoming = root / "incoming"
            incoming.mkdir()
            p = incoming / unicodedata.normalize("NFD", "1 кейс.pdf")
            p.write_bytes(b"%PDF-synthetic-fixture")
            artifacts = []
            import_directory(root, incoming, artifacts, register)
            before = copy.deepcopy(artifacts)
            original = root / artifacts[0]["local_path"]
            original.unlink()
            import_directory(root, incoming, artifacts, register)
            self.assertEqual(artifacts, before)
            self.assertEqual(original.read_bytes(), p.read_bytes())

    def bundle(self, path, *, bad_hash=False, unsafe=False):
        body = b"%PDF-synthetic-original"
        filename = "LTG_Cargo_2022.pdf"
        meta = {"file": filename, "bytes": len(body), "sha256": "0" * 64 if bad_hash else digest(body),
                "source_url": "https://example.invalid/report", "period": "2022", "unit": "млн т", "limitation": "fixture"}
        with ZipFile(path, "w") as z:
            z.writestr("../" + filename if unsafe else "исходники/" + filename, body)
            z.writestr("A1_коридор_манифест.json", json.dumps({"files": [meta]}))

    def test_identical_original_in_two_archives_has_two_locations(self):
        with TemporaryDirectory() as name:
            root, artifacts = Path(name), []
            for filename in BUNDLES:
                path = root / filename
                self.bundle(path)
                # Контейнеры должны различаться, исходный PDF — совпадать.
                with ZipFile(path, "a") as z:
                    z.writestr(filename + ".md", "synthetic archive context")
                import_bundle(root, path, artifacts, register)
            pdfs = [a for a in artifacts if a["format"] == "pdf"]
            self.assertEqual(len(pdfs), 1)
            self.assertEqual(len(pdfs[0]["acquisition_locations"]), 2)
            before = copy.deepcopy(artifacts)
            import_bundle(root, root / next(iter(BUNDLES)), artifacts, register)
            self.assertEqual(artifacts, before)

    def test_bad_manifest_or_unsafe_path_registers_nothing(self):
        for options in ({"bad_hash": True}, {"unsafe": True}):
            with self.subTest(options=options), TemporaryDirectory() as name:
                root, artifacts = Path(name), []
                path = root / next(iter(BUNDLES))
                self.bundle(path, **options)
                with self.assertRaises(ValueError):
                    import_bundle(root, path, artifacts, register)
                self.assertEqual(artifacts, [])
                self.assertFalse((root / "data/raw").exists())


def regional_fixture():
    header = dict(zip("ABCDEFGHIJK", ["Раздел", "Код", "Показатель", "Подраздел", "Год", "Значение", "Единица", "Источник", "Комментарий", "Версия", "Дубль ключа"]))
    def row(value, unit="Миллионов тонн", source="Издание A", code="Y477110293"):
        return dict(zip("ABCDEFGHIJK", ["Транспорт", code, "Синтетическая масса", "CD", "2022", value, unit, source, "ND", "test-v1", ""]))
    return [("Все_данные_parquet", {1: header, 2: row("2"), 3: row("3", source="Издание B"),
                                     4: row("-77777777", code="missing_fixture"),
                                     5: row("7", unit="ND", code="metadata_fixture"),
                                     6: row("-2", unit="Рубли", code="signed_fixture")})]


class RegionalTests(unittest.TestCase):
    def test_codes_metadata_signed_values_and_versions_are_preserved(self):
        issues = []
        rows, profile = regional(regional_fixture(), {"source_id": "synthetic"}, "test", issues)
        self.assertEqual([r["value"] for r in rows[:2]], ["2000000", "3000000"])
        self.assertTrue(all("conflicting_versions" in r["quality_flags"] for r in rows[:2]))
        self.assertEqual(rows[2]["original_value"], "-77777777")
        self.assertEqual(rows[2]["value"], "")
        self.assertEqual(rows[3]["value"], "7")
        self.assertEqual(rows[3]["subsection"], "CD")
        self.assertEqual(rows[3]["comment"], "ND")
        self.assertEqual(rows[4]["value"], "-2")
        self.assertEqual(profile["conflicting_version_keys"], 1)

    def test_different_dimensions_are_not_conflicting_versions(self):
        sheets = regional_fixture()
        sheets[0][1][3]["G"] = "Миллионов тонно-километров"
        rows, profile = regional(sheets, {"source_id": "synthetic"}, "test", [])
        self.assertEqual(profile["conflicting_version_keys"], 0)
        self.assertEqual(rows[1]["unit"], "tonne_km")


class ArchiveRowsTests(unittest.TestCase):
    def test_lower_bound_and_wagon_form_remain_separate_from_rail(self):
        row = {"месяц": "2023-01", "направление": "ввоз", "тонны_как_опубликовано": "100",
               "точность": "строго больше", "охват": "в Калининград; только груз в вагонах", "источник_URL": "https://example.invalid/news"}
        artifact = {"dataset_kind": "ferry_monthly", "source_id": "synthetic"}
        rows, _ = parse_archive_csv(artifact, [row], "test", {row["источник_URL"]: "synthetic_html"}, [])
        self.assertEqual(rows[0]["precision"], "lower_bound")
        self.assertEqual(rows[0]["mode"], "sea")
        self.assertEqual(rows[0]["direction_scope"], "inbound")
        self.assertEqual(rows[0]["transport_form"], "груз в вагонах")
        self.assertEqual(rows[0]["upstream_source_ids"], "synthetic_html")

    def test_ltg_all_transit_is_not_corridor_estimate(self):
        r = {"год": "2025", "международные_млн_т": "10", "весь_транзит_млн_т": "5",
             "коридор_опубликованное_млн_т": "", "доля_коридора_в_международных_процентов": "40",
             "толкование_и_ограничение": "synthetic", "источник_URL": "https://example.invalid/ltg"}
        rows, _ = parse_archive_csv({"source_id": "synthetic", "dataset_kind": "ltg_annual"}, [r], "test", {}, [])
        by_series = {r["series_id"]: r for r in rows}
        self.assertEqual(by_series["ltg_all_transit"]["value"], "5000000")
        estimate = by_series["ltg_corridor_share_estimate"]
        self.assertEqual(estimate["value"], "4000000")
        self.assertEqual(estimate["status"], "estimated")
        self.assertTrue(estimate["formula"])
        self.assertNotIn("ltg_kaliningrad_corridor", by_series)

    def test_ro_ro_in_source_cargo_list_is_a_transport_form(self):
        row = {"месяц": "2023-01", "направление": "ввоз", "тонны": "50", "группа_как_в_публикации": "накатная техника"}
        rows, _ = parse_archive_csv({"source_id": "synthetic", "dataset_kind": "ferry_cargo"}, [row], "test", {}, [])
        self.assertEqual(rows[0]["cargo_group"], "")
        self.assertEqual(rows[0]["transport_form"], "накатная техника")

    def test_border_counts_have_no_mass_or_corridor_direction(self):
        r = {"year": "2024", "month": "1", "crossing": "Тестовый КПП", "direction": "в соседнюю страну",
             "truck_crossings": "12", "unit": "пересечения грузовых автомобилей", "source_file": "fixture.xlsx",
             "source_sheet": "fixture", "source_cell": "A1", "source_url": "https://example.invalid/border"}
        rows, borders = parse_archive_csv({"source_id": "synthetic", "dataset_kind": "border_crossings"}, [r], "test", {}, [])
        self.assertEqual(rows, [])
        self.assertEqual(borders[0]["truck_crossings"], "12")
        self.assertNotIn("value_tonnes", borders[0])
        self.assertNotIn("direction_scope", borders[0])


def port_fixture(month=1, total="5", previous="3"):
    title = f"Объём перевалки за январь 2025 г." if month == 1 else f"Объём перевалки за {month} месяца 2025 г."
    rows = {1: {"A": title}, 2: {"A": " каботаж", "Y": "тыс. тонн"},
            3: {"A": "Бассейн / Порт", "B": "Год", "C": "Всего\nгрузов"},
            4: {"A": "Порт Калининград"},
            5: {"A": "Тестовое управление бассейнового филиала", "B": "24", "C": previous},
            6: {"A": "", "B": "25", "C": total},
            7: {"A": "", "B": "%", "C": "100"},
            8: {"A": "Итого Порт Калининград", "B": "24", "C": previous},
            9: {"A": "", "B": "25", "C": total},
            10: {"A": "Итого Балтийский бассейн", "B": "25", "C": "999999"}}
    return [("synthetic_sheet", rows)]


class PortTests(unittest.TestCase):
    def artifact(self, month):
        return {"source_id": "synthetic_" + str(month), "publication_year": 2025, "publication_month": month}

    def test_ports_comparative_year_and_percent_are_separate(self):
        rows = parse_port(port_fixture(), self.artifact(1), "test")
        self.assertEqual(len(rows), 4)
        self.assertEqual({r["period"] for r in rows}, {"2024-01", "2025-01"})
        self.assertEqual({r["measurement_scope"] for r in rows}, {"operator", "port_total"})
        self.assertEqual({r["value"] for r in rows}, {"3000", "5000"})
        self.assertNotIn("Итого Балтийский бассейн", {r["operator"] for r in rows})
        self.assertIn("Тестовое управление бассейнового филиала", {r["operator"] for r in rows})

    def test_monthly_difference_uses_main_year_and_preserves_two_sources(self):
        rows = parse_port(port_fixture(), self.artifact(1), "test") + parse_port(port_fixture(2, "8"), self.artifact(2), "test")
        monthly = port_monthly(rows, {self.artifact(i)["source_id"]: self.artifact(i) for i in (1, 2)}, [])
        self.assertEqual({r["period"] for r in monthly}, {"2025-01", "2025-02"})
        february = [r for r in monthly if r["period"] == "2025-02"]
        self.assertTrue(all(r["value"] == "3000" and r["status"] == "estimated" for r in february))
        self.assertTrue(all(r["previous_source_id"] == "synthetic_1" for r in february))

    def test_missing_predecessor_and_negative_difference_do_not_create_observations(self):
        rows = parse_port(port_fixture(2), self.artifact(2), "test")
        issues = []
        self.assertEqual(port_monthly(rows, {"synthetic_2": self.artifact(2)}, issues), [])
        self.assertEqual({r["issue"] for r in issues}, {"missing_previous_month"})
        rows += parse_port(port_fixture(), self.artifact(1), "test")
        for row in rows:
            if row["period"] == "2025-02":
                row["value"] = "1000"
        issues = []
        out = port_monthly(rows, {"synthetic_1": self.artifact(1), "synthetic_2": self.artifact(2)}, issues)
        self.assertTrue(all(r["period"] == "2025-01" for r in out))
        self.assertEqual({r["issue"] for r in issues}, {"negative_difference"})

    def test_duplicate_snapshot_is_not_selected_arbitrarily(self):
        rows = parse_port(port_fixture(), self.artifact(1), "test")
        issues = []
        self.assertEqual(port_monthly(rows + rows, {"synthetic_1": self.artifact(1)}, issues), [])
        self.assertEqual({r["issue"] for r in issues}, {"ambiguous_snapshot"})


class PartialBuildTests(unittest.TestCase):
    def test_partial_package_rebuilds_offline_with_missing_catalog_original(self):
        with TemporaryDirectory() as name:
            root = Path(name) / "source"
            root.mkdir()
            artifacts = make_registry(root)
            catalog = next(a for a in artifacts if a["source_key"] == "hackathon_catalog")
            (root / catalog["local_path"]).unlink()
            supplied_catalog = b'catalog_id,title,description,tracks,url,purpose,priority,review_status,limitations\n1,synthetic,,,https://example.invalid,reference,low,discovered,unverified\n'
            (root / "data/source_catalog.csv").write_bytes(supplied_catalog)
            for relative in ("docs/data-preparation.md", "docs/contracts.md", "data/README.md"):
                p = root / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, p)
            with self.assertRaises(ValueError):
                build(root)
            first = build(root, available_only=True)
            self.assertEqual(first["build_scope"], "available_only")
            self.assertEqual(first["unavailable_source_ids"], [catalog["source_id"]])
            self.assertEqual((root / "data/source_catalog.csv").read_bytes(), supplied_catalog)
            self.assertEqual(verify(root), [])
            output = package(root, Path(name) / "handoff.zip")
            destination = Path(name) / "recipient"
            with ZipFile(output) as z:
                z.extractall(destination)
            self.assertFalse((destination / catalog["local_path"]).exists())
            self.assertEqual(verify(destination), [])
            rebuilt = build(destination, available_only=True)
            self.assertEqual(first, rebuilt)

    def test_available_only_still_rejects_corruption(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            artifacts = make_registry(root)
            (root / artifacts[0]["local_path"]).write_bytes(b"synthetic-corruption")
            with self.assertRaises(ValueError):
                build(root, available_only=True)


if __name__ == "__main__":
    unittest.main()
