"""Вымышленные таблицы: география, единицы, интервалы и воспроизводимая передача."""

import csv
import io
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from pipeline.far_east import CODE, Builder, safe_member, verify
from pipeline.far_east_tables import (
    base_row,
    normalize_unit,
    number,
    parse_pivot_customs,
    period_info,
    select_rows,
)


class GeographyTests(unittest.TestCase):
    def test_mixed_blocks_reset_at_every_non_target_region(self):
        rows = {
            1: ["Экспорт важнейших товаров"],
            2: ["REGION", "NAMEKOD", "NAMETOV"],
            3: ["Камчатский край", "", ""],
            4: ["", "03", "Рыба"],
            5: ["Чукотский АО", "", ""],
            6: ["", "03", "Другая рыба"],
            7: ["Приморский край", "", ""],
            8: ["", "03", "Рыба"],
            9: ["Сахалинская область", "", ""],
            10: ["", "03", "Иная рыба"],
        }
        _, selected = select_rows(rows, "Камчатский.xls")
        self.assertEqual([r[0] for r in selected], [3, 4, 7, 8])

    def test_direct_summary_does_not_extend_region_to_next_row(self):
        rows = {
            1: ["Наименование региона", "Стоимость"],
            4: ["Амурская область", "1"],
            5: ["Камчатская область", "2"],
            6: ["Магаданская область", "3"],
            7: ["Приморский край", "4"],
            8: ["Чукотский АО", "5"],
        }
        headers, selected = select_rows(rows, "сводка.xls")
        self.assertEqual([r[0] for r in headers], [1])
        self.assertEqual([r[0] for r in selected], [5, 7])
        self.assertEqual(selected[0][2], "Камчатская область")

    def test_old_pivot_filter_is_not_mistaken_for_data_region_column(self):
        rows = {
            1: ["Камчатский край - Экспорт товаров"],
            4: ["", "REGION", "PERIOD", "Данные"],
            8: ["Код ТНВЭД и наименование товара", "количество"],
            9: ["03 - Рыба", ""],
            10: ["КИТАЙ", "25"],
        }
        _, selected = select_rows(rows, "неизвестное имя.xls")
        self.assertEqual([r[0] for r in selected], [9, 10])


class MeasurementTests(unittest.TestCase):
    def test_missing_and_special_codes_are_not_zero(self):
        for raw in ("", "ND", "CD", "—", "-77777777.0", "-88888888.0", "-99999999", "-77777777.000"):
            self.assertEqual(number(raw)[0], "")
        self.assertEqual(number("0"), ("0", ""))
        self.assertEqual(number("1 234,5", "1000"), ("1234500", ""))

    def test_transport_work_and_mass_remain_different(self):
        self.assertEqual(normalize_unit("Миллионов тонн"), ("tonnes", "1000000"))
        self.assertEqual(normalize_unit("Миллионов тонно-километров"), ("tonne_km", "1000000"))
        self.assertEqual(normalize_unit("TEU"), ("TEU", "1"))

    def test_ytd_quarter_and_annual_keep_distinct_intervals(self):
        ytd = period_info("01.01.2020-30.06.2020")
        quarter = period_info("2 квартал", "2020")
        self.assertEqual(ytd, ("2020-06", "month", "2020-01-01", "2020-06-30", "year_to_date"))
        self.assertEqual(quarter, ("2020-06", "month", "2020-04-01", "2020-06-30", "quarter"))
        self.assertEqual(period_info("01.01.2020-31.12.2020")[1], "year")
        with self.assertRaises(ValueError):
            period_info("01.01.2020-31.02.2020")

    def test_vintage_and_historical_geography_are_not_silently_merged(self):
        args = (
            "Камчатская область",
            period_info("2005"),
            "freight_mass",
            "2",
            "Миллионов тонн",
            {"source_id": "synthetic"},
            "row=1",
            "test",
        )
        old = base_row(*args, source_title="Издание А")
        new = base_row(*args, source_title="Издание Б")
        self.assertNotEqual(old["series_id"], new["series_id"])
        self.assertIn("historical_geography_review_required", old["quality_flags"])
        self.assertEqual(old["value"], "2000000")
        self.assertEqual(old["direction"], "")
        self.assertEqual(old["available_at"], "")

    def test_customs_quantity_column_is_not_used_as_mass(self):
        rows = {
            1: ["", "Экспорт важнейших товаров"],
            2: ["", "", "", "01.01.2020-30.06.2020"],
            3: ["REGION", "NAMEKOD", "NAMETOV", "Сумма по полю KOL", "Сумма по полю TONN", "Сумма по полю TDOLL"],
            4: ["Приморский край", "", "", "", "", ""],
            5: ["", "0105", "Птица", "200", "1.2", "4"],
        }
        _, selected = select_rows(rows, "test.xls")
        values = parse_pivot_customs("ЭК важнейшие товары", rows, selected, {"source_id": "synthetic"}, "test")
        self.assertEqual(len(values), 2)
        self.assertEqual({r["value"] for r in values}, {"1.2", "4000"})
        self.assertEqual({r["cargo_code"] for r in values}, {"0105"})
        self.assertTrue(all(r["period_basis"] == "year_to_date" for r in values))


class HandoffTests(unittest.TestCase):
    def test_unsafe_archive_paths_are_rejected(self):
        for name in ("../outside.xls", "/tmp/outside.xls", "C:/outside.xls", "a/../../b.csv"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_member(name)

    def test_rebuild_is_identical_and_tampering_is_detected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = Path(__file__).resolve().parents[1]
            for relative in CODE:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / relative, destination)
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "region",
                    "year",
                    "transport_type",
                    "metric",
                    "indicator_code",
                    "indicator_name",
                    "value",
                    "unit",
                    "source",
                ]
            )
            for geo in ("Камчатский край", "Приморский край", "Чукотский АО"):
                writer.writerow(
                    [
                        geo,
                        "2020",
                        "автомобильный",
                        "грузы",
                        "fake",
                        "Перевозки грузов автомобильным транспортом",
                        "2.5",
                        "Миллионов тонн",
                        "Вымышленное издание",
                    ]
                )
            archive = root / "synthetic.zip"
            with ZipFile(archive, "w") as z:
                z.writestr("perevozki_kamchatka_primorye_2001_2024.csv", stream.getvalue())
                z.writestr("duplicate.csv", stream.getvalue())
                z.writestr("Чукотский.xls", b"excluded without parsing")
            first = Builder(root, archive).run()
            self.assertEqual(verify(root), [])
            second = Builder(root, archive).run()
            self.assertEqual(first, second)
            renamed = root / "renamed-input.zip"
            archive.rename(renamed)
            self.assertEqual(first, Builder(root, renamed).run())
            out = root / "data/processed/far_east"
            with (out / "transport_long.csv").open(encoding="utf-8-sig") as f:
                values = list(csv.DictReader(f))
            self.assertEqual({r["region"] for r in values}, {"kamchatka", "primorye"})
            self.assertEqual(len(values), 2)
            with (out / "freight_annual_panel.csv").open(encoding="utf-8-sig") as f:
                panel = list(csv.DictReader(f))
            self.assertTrue(all(r["rail_dispatch_tonnes"] == "" for r in panel))
            self.assertTrue(all(r["road_freight_tonnes"] == "2500000" for r in panel))
            registry = json.loads((out / "source_registry.json").read_text())
            self.assertEqual(len(registry["artifacts"]), 2)  # ZIP плюс одни уникальные байты CSV.
            with (out / "transport_long.csv").open("a") as f:
                f.write("\n")
            self.assertTrue(any("SHA-256" in e for e in verify(root)))


if __name__ == "__main__":
    unittest.main()
