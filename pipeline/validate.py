"""Минимальная проверка структуры нормализованной таблицы, без импутации."""
import csv
import math
import re
import sys
from pathlib import Path

REQUIRED = {"period", "period_granularity", "origin", "destination", "direction", "mode", "cargo_group", "value_tonnes", "status", "source_id"}
MODES = {"sea", "rail_land", "road", "air"}
STATUSES = {"observed", "estimated", "synthetic"}


def validate(path: Path) -> list[str]:
    if not path.is_file():
        return [f"Файл не найден: {path}"]
    errors: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            return [f"Нет столбцов: {', '.join(sorted(missing))}"]
        for line, row in enumerate(reader, start=2):
            expected_period = r"\d{4}-\d{2}" if row["period_granularity"] == "month" else r"\d{4}"
            if row["period_granularity"] not in {"month", "year"} or not re.fullmatch(expected_period, row["period"]):
                errors.append(f"Строка {line}: неверный период или шаг времени")
            if not all(row[field].strip() for field in ("origin", "destination", "cargo_group", "source_id")):
                errors.append(f"Строка {line}: не заполнены происхождение, назначение, группа или источник")
            if row["mode"] not in MODES or row["status"] not in STATUSES or row["direction"] not in {"inbound", "outbound"}:
                errors.append(f"Строка {line}: неизвестный вид, статус или направление")
            if row["value_tonnes"].strip():
                try:
                    value = float(row["value_tonnes"])
                    if not math.isfinite(value) or value < 0:
                        errors.append(f"Строка {line}: масса должна быть конечной и неотрицательной")
                except ValueError:
                    errors.append(f"Строка {line}: масса не число")
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python -m pipeline.validate <файл.csv>")
    issues = validate(Path(sys.argv[1]))
    print("\n".join(issues) if issues else "Структура и базовые значения проверены")
    raise SystemExit(bool(issues))
