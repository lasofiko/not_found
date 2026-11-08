"""Все значения и реквизиты ниже вымышлены; используются только во временных тестах."""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from zipfile import ZipFile

from pipeline.tables import numeric, normalize_transit, parse_export, read_xlsx, transit_summary, transport_mode


def export_fixture():
    units = dict(zip("BCDEFGHIJ", ["Кол-во предприятий, шт", "Вес, тонн", "Стоимость, тыс.долл.США"] * 3))
    rows = {3: {"A": "Итоги внешней торговли за 2020-2022 годы"},
            4: {"B": "(весь экспорт)"},
            5: {"B": "январь-январь 2021 года", "E": "январь-январь 2022 года"},
            6: units, 7: {"A": "ИТОГО"},
            8: dict(zip("ABCDEFGHIJ", ["Всего", "10", "1000", "30", "12", "1200", "40", "120", "120", "133.3"])),
            9: {"A": "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ"},
            10: dict(zip("ABCDEFGHIJ", ["Всего", "5", "100", "1.5", "6", "200", "2.5", "120", "200", "166.7"]))}
    return [("Лист1", rows), ("Лист2", {}), ("Лист3", {})]


def artifact():
    return {"source_key": "exports_all_jan2022", "source_id": "synthetic_export_fixture"}


def transit_fixture(item=1, gross=100):
    values = [""] * 56
    entries = {0: "SENSITIVE_DECLARATION", 1: "NATIONAL", 2: "TRANSIT", 3: 2,
               7: 900, 8: 900, 9: 0, 10: 4, 11: "31", 12: "Состав транспортных средств (тягач с полуприцепом или прицепом)",
               18: "SENSITIVE_COMPANY", 19: "SENSITIVE_ADDRESS", 20: "SENSITIVE_TAX_ID",
               24: "LT", 26: "RU", 28: "123", 29: "Тестовая таможня отправления", 30: "456", 31: "Тестовая таможня назначения",
               33: "31", 34: "Состав транспортных средств (тягач с полуприцепом или прицепом)", 35: item,
               36: "2204100000", 38: gross, 46: 123.5, 48: "SENSITIVE_RECIPIENT", 49: "SENSITIVE_RECIPIENT_ADDRESS",
               50: "SENSITIVE_RECIPIENT_TAX", 54: "SENSITIVE_CONTAINER", 55: "SENSITIVE_VEHICLE"}
    for col, value in entries.items():
        values[col] = value
    return values


def transit(records):
    return normalize_transit(records, {"source_id": "synthetic_transit_fixture"}, "test-version",
                             lambda value: datetime(2016, 12, 1) + timedelta(days=value))


class ExportTests(unittest.TestCase):
    def test_periods_come_from_columns_and_geography_is_not_added_twice(self):
        rows, growth, issues, profile = parse_export(export_fixture(), artifact(), "test-version")
        self.assertEqual({r["period"] for r in rows}, {"2021-01", "2022-01"})
        self.assertEqual(len(rows), 4)
        self.assertEqual(profile["empty_sheets"], 2)
        self.assertEqual(len([r for r in rows if r["geography_level"] == "country"]), 2)
        self.assertEqual(issues, [])

    def test_currency_scale_is_separate_from_mass(self):
        rows, _, _, _ = parse_export(export_fixture(), artifact(), "test-version")
        row = next(r for r in rows if r["reporting_geography"] == "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ" and r["period"] == "2022-01")
        self.assertEqual(row["trade_value_usd"], "2500")
        self.assertEqual(row["weight_tonnes"], "200")
        self.assertNotIn("direction", row)

    def test_growth_index_and_zero_base_are_not_invented_changes(self):
        sheets = export_fixture()
        sheets[0][1][10]["C"] = "0"
        sheets[0][1][10]["I"] = "0"
        _, checks, _, _ = parse_export(sheets, artifact(), "test-version")
        check = next(r for r in checks if r["metric"] == "weight_tonnes" and r["reporting_geography"] == "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ")
        self.assertEqual(check["check_status"], "zero_base")
        self.assertEqual(check["calculated_growth_index_pct"], "")
        self.assertEqual(check["reported_growth_index_pct"], "0")

    def test_growth_disagreement_keeps_original_values(self):
        sheets = export_fixture()
        sheets[0][1][10]["I"] = "75"
        rows, checks, _, _ = parse_export(sheets, artifact(), "test-version")
        self.assertEqual(next(r for r in checks if r["reported_growth_index_pct"] == "75")["check_status"], "displayed_values_differ")
        self.assertEqual(next(r for r in rows if r["source_locator"] == "Лист1!E10:G10")["weight_tonnes"], "200")

    def test_changed_unit_is_rejected(self):
        sheets = export_fixture()
        sheets[0][1][6]["F"] = "Вес, кг"
        with self.assertRaises(ValueError):
            parse_export(sheets, artifact(), "test-version")

    def test_unknown_codes_are_not_zero_and_float_noise_only_is_removed(self):
        for value in ("ND", "CD", "-77777777", "NaN", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                numeric(value)
        self.assertEqual(numeric(""), "")
        self.assertEqual(numeric("0"), "0")
        self.assertEqual(numeric("71.400000000000006", percent=True), "71.4")
        self.assertEqual(numeric("71.4123", percent=True), "71.4123")

    def test_xlsx_unicode_zero_and_uncached_formula(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "fixture.xlsx"
            def write_sheet(cell):
                with ZipFile(path, "w") as z:
                    z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Лист1" sheetId="1" r:id="r1"/></sheets></workbook>')
                    z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
                    z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">' + cell + '</row></sheetData></worksheet>')
            write_sheet('<c r="A1" t="inlineStr"><is><t>КАЛИНИНГРАД</t></is></c><c r="B1"><v>0</v></c>')
            self.assertEqual(read_xlsx(path)[0], ("Лист1", {1: {"A": "КАЛИНИНГРАД", "B": "0"}}))
            write_sheet('<c r="A1"><f>1+1</f></c>')
            with self.assertRaises(ValueError):
                read_xlsx(path)


class TransitTests(unittest.TestCase):
    def test_several_items_are_not_duplicate_declarations_or_repeated_total_mass(self):
        rows, issues, profile = transit([(3, transit_fixture()), (4, transit_fixture(2, 150))])
        self.assertEqual(profile["declarations"], 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(issues, [])
        self.assertEqual(transit_summary(rows)[0]["item_gross_sum_tonnes"], "0.25")

    def test_missing_net_is_not_gross_or_zero(self):
        rows, _, _ = transit([(3, transit_fixture())])
        self.assertEqual(rows[0]["item_gross_tonnes"], "0.1")
        self.assertEqual(rows[0]["item_net_kg"], "")
        self.assertEqual(transit_summary(rows)[0]["item_net_sum_tonnes"], "")

    def test_identifying_fields_are_not_in_clean_output(self):
        rows, _, _ = transit([(3, transit_fixture())])
        self.assertNotIn("SENSITIVE", json.dumps(rows))
        self.assertEqual(len(rows[0]["declaration_id_hash"]), 64)
        self.assertEqual(rows[0]["transit_days"], 4)

    def test_only_exact_repeats_are_removed(self):
        rows, issues, profile = transit([(3, transit_fixture()), (4, transit_fixture())])
        self.assertEqual(len(rows), 1)
        self.assertEqual(profile["exact_duplicates_removed"], 1)
        self.assertEqual(issues[0]["related_locator"], "Транзит!A3:BD3")

    def test_conflicts_are_retained_and_both_rows_flagged(self):
        rows, _, profile = transit([(3, transit_fixture()), (4, transit_fixture(1, 200))])
        self.assertEqual(len(rows), 2)
        self.assertEqual(profile["conflicting_keys"], 1)
        self.assertTrue(all("conflicting_declaration_item_key" in r["quality_flags"] for r in rows))

    def test_bad_dates_or_negative_mass_stop_processing(self):
        for col, value in ((10, -1), (38, -1)):
            values = transit_fixture()
            values[col] = value
            with self.subTest(column=col), self.assertRaises(ValueError):
                transit([(3, values)])

    def test_road_combinations_and_ambiguous_water_are_preserved(self):
        self.assertEqual(transport_mode("Состав транспортных средств (тягач с полуприцепом)"), "road")
        self.assertEqual(transport_mode("Морской/речной транспорт"), "water")
        self.assertEqual(transport_mode("Железнодорожный транспорт"), "rail")


if __name__ == "__main__":
    unittest.main()
