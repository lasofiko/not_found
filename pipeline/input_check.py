"""Проверка пользовательского CSV с обязательным происхождением; без импорта в цель."""
import argparse
import csv
from pathlib import Path

from pipeline.common import OBSERVATION_FIELDS, ROOT
from pipeline.validate import validate

TRACE_FIELDS = ["source_locator", "original_value", "original_unit", "conversion_factor"]
INPUT_FIELDS = OBSERVATION_FIELDS + ["route_id", "in_wagon_on_ferry", *TRACE_FIELDS,
                                  "quality_note", "data_version", "formula", "shipment_id"]


def check_input(path: Path, *, root: Path = ROOT, observed_only: bool = False) -> list[str]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            if set(OBSERVATION_FIELDS + TRACE_FIELDS) - set(headers):
                return ["Нет обязательных полей наблюдения и происхождения; используйте observations-template.csv"]
            rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            return ["Число ячеек не совпадает с заголовком"]
        ids = {r["source_id"] for r in rows}
        errors = validate(path, registry=root / "data/source_registry.json", root=root, corridor=True,
                          require_observations=True, observed_only=observed_only, source_ids=ids)
        seen_shipments = {}
        for line, row in enumerate(rows, 2):
            if not row["source_locator"].strip():
                errors.append(f"Строка {line}: нужно указать конкретную ячейку/таблицу/страницу source_locator")
            if row["original_unit"] not in {"тонн", "тыс. тонн", "млн тонн"}:
                errors.append(f"Строка {line}: недопустимая единица массы; TEU/вагоны/машины не переводятся универсальным коэффициентом")
            if row["status"] == "estimated" and not row.get("formula", "").strip():
                errors.append(f"Строка {line}: estimated требует formula")
            shipment = row.get("shipment_id", "").strip()
            if shipment:
                if shipment in seen_shipments:
                    errors.append(f"Строка {line}: отправка {shipment} уже учтена в строке {seen_shipments[shipment]}; двойной счёт запрещён")
                seen_shipments[shipment] = line
        return errors
    except (OSError, UnicodeError, csv.Error, ValueError, KeyError) as error:
        return [f"Не удалось проверить CSV: {error}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--observed-only", action="store_true")
    args = parser.parse_args()
    errors = check_input(args.path, root=args.root.resolve(), observed_only=args.observed_only)
    print("\n".join(errors) if errors else
          "Структура, единицы и источники проверены. География показателя, методика и утверждение цели требуют приёмки; импорт не выполнялся.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
