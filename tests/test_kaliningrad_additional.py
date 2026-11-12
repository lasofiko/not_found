"""Вымышленные таможенные формы: единицы, сравнительные периоды и передача."""

import csv
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from pipeline.kaliningrad_additional import CODE, Builder, safe_name, verify
from pipeline.kaliningrad_customs import docx_content, parse_sheet, period


def goods():
    return {
        1: ["Форма 1-ТС (регион)"] + [""] * 11,
        3: ["Экспорт и импорт важнейших товаров"] + [""] * 11,
        4: ["27000-КАЛИНИНГРАДСКАЯ ОБЛАСТЬ"] + [""] * 11,
        5: ["январь - сентябрь 2021 года"] + [""] * 11,
        6: [
            "Код ТНВЭД",
            "Наименование товара",
            "Единица измерения",
            "Всего",
            "",
            "",
            "Дальнее зарубежье",
            "",
            "",
            "СНГ",
            "",
            "",
        ],
        7: [
            "",
            "",
            "",
            "вес, тонн",
            "кол-во",
            "стоимость, тыс. долл.США",
            "вес, тонн",
            "кол-во",
            "стоимость, тыс. долл.США",
            "вес, тонн",
            "кол-во",
            "стоимость, тыс. долл.США",
        ],
        8: ["ЭКСПОРТ"] + [""] * 11,
        9: ["0302", "Вымышленный товар", "КГ", "2.5", "1000", "4", "2", "900", "3", "0.5", "100", "1"],
    }


def parse(rows, sheet="Форма 1-ТС"):
    return parse_sheet(sheet, rows, "synthetic_source", "test")


def write_xlsx(path, rows):
    from xml.sax.saxutils import escape

    from pipeline.far_east_tables import column_name

    cells = []
    for n, vals in rows.items():
        inner = "".join(
            f'<c r="{column_name(c + 1)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for c, v in enumerate(vals)
            if v
        )
        cells.append(f'<row r="{n}">{inner}</row>')
    with ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Тест" sheetId="1" r:id="r1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(cells)
            + "</sheetData></worksheet>",
        )


class ParsingTests(unittest.TestCase):
    def test_weight_header_overrides_supplementary_quantity_unit(self):
        values = parse(goods())
        mass = next(r for r in values if r["source_locator"].endswith("!D9"))
        self.assertEqual((mass["value"], mass["unit"]), ("2.5", "tonnes"))
        self.assertNotIn("1000", [r["value"] for r in values if r["metric"] == "trade_goods_mass"])
        self.assertEqual(next(r for r in values if r["source_locator"].endswith("!F9"))["value"], "4000")
        self.assertEqual(mass["cargo_code"], "0302")

    def test_mixed_weight_quantity_is_not_claimed_to_be_tonnes(self):
        rows = goods()
        rows[7] = [v.replace("вес, тонн", "Вес тонн / кол-во") for v in rows[7]]
        self.assertEqual(parse(rows), [])

    def test_first_commodity_row_is_preserved(self):
        rows = {
            1: ["Товарная структура экспорта и импорта"],
            5: ["январь - сентябрь 2021 года"],
            6: ["(тыс. USD)"],
            7: ["Код", "Товар", "ВСЕГО", ""],
            8: ["", "", "ЭКСПОРТ", "ИМПОРТ"],
            9: ["01-24", "Вымышленная группа", "1", "2"],
        }
        values = parse(rows)
        self.assertEqual(len(values), 2)
        self.assertEqual({r["value"] for r in values}, {"1000", "2000"})

    def test_comparative_years_are_taken_from_each_column(self):
        rows = {
            1: ["Общие итоги внешней торговли"],
            5: ["январь - сентябрь 3 квартал 2021 года"],
            6: ["(тыс. USD)"],
            7: ["", "2020 год", "", "2021 год", ""],
            8: ["", "3 квартал", "январь-сентябрь", "3 квартал", "январь-сентябрь"],
            9: ["ЭКСПОРТ", "1", "2", "3", "4"],
            10: ["САЛЬДО", "-1", "-2", "-3", "-4"],
        }
        values = parse(rows)
        self.assertEqual([r["period"] for r in values[:4]], ["2020-09", "2020-09", "2021-09", "2021-09"])
        self.assertEqual([r["period_basis"] for r in values[:4]], ["quarter", "year_to_date"] * 2)
        self.assertTrue(all(r["value"].startswith("-") for r in values[4:]))

    def test_currency_and_scale_require_an_explicit_unit_on_identical_forms(self):
        for label, unit, factor, expected in (
            ("USD", "usd", "1", "2"),
            ("тыс. USD", "usd", "1000", "2000"),
            ("рублей", "rub", "1", "2"),
            ("млн рублей", "rub", "1000000", "2000000"),
            ("", "", "", ""),
            ("доллары", "", "", ""),
        ):
            rows = {1: ["Товарная структура экспорта и импорта"],
                    5: ["январь - сентябрь 2021 года"], 6: [label],
                    7: ["Код", "Товар", "ВСЕГО", ""],
                    8: ["", "", "ЭКСПОРТ", "ИМПОРТ"],
                    9: ["01-24", "Вымышленная группа", "2", "2"]}
            with self.subTest(label=label):
                values = parse(rows)
                self.assertEqual(len(values), 2)
                self.assertEqual({(r["unit"], r["conversion_factor"], r["value"]) for r in values},
                                 {(unit, factor, expected)})
                self.assertEqual({r["original_unit"] for r in values}, {label})
                self.assertTrue(all(r["original_value"] == "2" and r["review_status"] == "pending" for r in values))
                if not unit:
                    self.assertTrue(all("unit_unconfirmed" in r["quality_flags"] for r in values))

    def test_unknown_column_unit_is_preserved_without_inheriting_usd(self):
        rows = goods()
        rows[7][5] = "стоимость, EUR"
        value = next(r for r in parse(rows) if r["source_locator"].endswith("!F9"))
        self.assertEqual((value["original_unit"], value["value"], value["unit"], value["conversion_factor"]),
                         ("стоимость, EUR", "", "", ""))

    def test_different_currencies_are_not_the_same_series(self):
        usd, rub = goods(), goods()
        for col in (5, 8, 11):
            usd[7][col] = 'стоимость, USD'
            rub[7][col] = 'стоимость, рублей'
        usd_value = next(r for r in parse(usd) if r['source_locator'].endswith('!F9'))
        rub_value = next(r for r in parse(rub) if r['source_locator'].endswith('!F9'))
        self.assertNotEqual(usd_value['series_id'], rub_value['series_id'])

    def test_ambiguous_caption_is_not_one_period(self):
        self.assertIsNone(period("январь-сентябрь 3 квартал 2021 года"))
        self.assertIsNone(period("3 квартал 2021/3 квартал 2020"))
        self.assertEqual(period("январь-декабрь 2020")[4], "year_to_date")
        self.assertEqual(period("в декабре 2014 года")[0], "2014-12")

    def test_docx_merged_value_is_not_repeated(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.docx"
            xml = (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                "<w:p><w:r><w:t>Текст источника</w:t></w:r></w:p><w:tbl><w:tr><w:tc>"
                '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>10</w:t></w:r></w:p>'
                "</w:tc></w:tr></w:tbl></w:body></w:document>"
            )
            with ZipFile(path, "w") as z:
                z.writestr("word/document.xml", xml)
            text, tables = docx_content(path)
            self.assertIn("Текст источника", text)
            self.assertEqual(tables, [("word_table_001", {1: ["10", ""]}, 2)])


class PackageTests(unittest.TestCase):
    def test_unsafe_paths_are_rejected(self):
        for name in ("../evil.xls", "/tmp/evil.xls", "C:\\evil.xls"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_repeat_rename_snapshot_and_corruption(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repo = Path(__file__).resolve().parents[1]
            for relative in CODE:
                dest = root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / relative, dest)
            source = root / "source.xlsx"
            write_xlsx(source, goods())
            archive = root / "synthetic.zip"
            with ZipFile(archive, "w") as z:
                z.write(source, "a.xlsx")
                z.write(source, "duplicate.xlsx")
            (root / "data").mkdir()
            existing = root / "data/source_registry.json"
            existing.write_text(json.dumps({"schema_version": "0.2", "artifacts": []}), encoding="utf-8")
            before = existing.read_bytes()
            first = Builder(root, archive).run()
            self.assertEqual(verify(root), [])
            self.assertEqual(first, Builder(root, archive).run())
            renamed = root / "renamed.zip"
            archive.rename(renamed)
            self.assertEqual(before, existing.read_bytes())
            existing.unlink()  # В новом комплекте достаточно сохранённого снимка реестра.
            self.assertEqual(first, Builder(root, renamed).run())
            out = root / "data/processed/kaliningrad_additional"
            with (out / "customs_long.csv").open(encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 6)
            with (out / "customs_long.csv").open("a") as f:
                f.write("\n")
            self.assertTrue(any("SHA-256" in e for e in verify(root)))


if __name__ == "__main__":
    unittest.main()
