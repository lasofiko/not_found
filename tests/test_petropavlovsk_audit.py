"""Периоды и контекст диагностики проверяются на синтетических ячейках."""
import csv
import gzip
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ml.audit_petropavlovsk import PORT, check_context, read_issue, regional_rows


def table():
    return [("Sheet", {
        1: {"A": "Объём перевалки за 6 мес. 2025"},
        2: {"A": " каботаж", "AA": "тыс. тонн"},
        3: {"A": "Бассейн / Порт", "B": "Год", "C": "Всего\nгрузов"},
        4: {"A": PORT},
        5: {"A": "Итого " + PORT, "B": "24", "C": "1"},
        6: {"A": "", "B": "25", "C": "2.5"},
    })]


class PetropavlovskAuditTests(unittest.TestCase):
    def test_later_issue_is_rejected_before_source_is_opened(self):
        with patch("ml.audit_petropavlovsk.sheets") as read:
            with self.assertRaisesRegex(ValueError, "Запрещённый год"):
                read_issue(Path("/unopened"), {"publication_year": 2026})
            read.assert_not_called()

    def test_current_total_accepts_line_break_in_header_and_normalizes_unit(self):
        context = check_context(table(), {"source_locator": "Sheet!C6", "value": "2500"}, 2025)
        self.assertEqual(context["year_cell"], "Sheet!B6")
        self.assertEqual(context["column_header"], "Всего грузов")

    def test_comparative_year_is_not_current_total(self):
        with self.assertRaisesRegex(ValueError, "года"):
            check_context(table(), {"source_locator": "Sheet!C5", "value": "1000"}, 2025)

    def test_operator_is_not_port_total(self):
        tables = table()
        tables[0][1][5]["A"] = "ООО Оператор"
        with self.assertRaisesRegex(ValueError, "итога порта"):
            check_context(tables, {"source_locator": "Sheet!C6", "value": "2500"}, 2025)

    def test_other_navigation_or_units_are_not_accepted(self):
        for replacement in ({"A": "Экспорт", "AA": "тыс. тонн"}, {"A": "Каботаж", "AA": "TEU"}):
            with self.subTest(replacement=replacement):
                tables = table()
                tables[0][1][2] = replacement
                with self.assertRaisesRegex(ValueError, "плавания или единицы"):
                    check_context(tables, {"source_locator": "Sheet!C6", "value": "2500"}, 2025)

    def test_regional_year_projection_does_not_parse_other_years(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "regional.csv.gz"
            columns = ["Виды грузов", "Виды плавания", "Единица измерения", "отправлено-прибыло", "Период",
                       "Субъекты РФ", "2024", "2025", "2026"]
            with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                for period in ("118 январь-декабрь", "125 январь-сентябрь"):
                    for operation in ("1 отправлено", "2 прибыло"):
                        writer.writerow(["57 всего грузов", "    70 каботаж", "169 тысяча тонн", operation, period,
                                         " 11300000 Камчатский край", "1.2", "2.3", "MUST_NOT_PARSE"])
            rows = regional_rows(path)
            self.assertEqual(len(rows), 8)
            self.assertEqual({r["period"] for r in rows}, {"2024", "2025"})
            self.assertEqual({r["value_tonnes"] for r in rows}, {"1200.0", "2300.0"})
            self.assertNotIn("MUST_NOT_PARSE", str(rows))
