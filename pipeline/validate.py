"""Проверка структуры, календаря, происхождения и направления наблюдений."""
import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from pipeline.common import OBSERVATION_FIELDS, ROOT, load_registry, verify_artifact
from pipeline.extract import mass

REQUIRED = set(OBSERVATION_FIELDS)
MODES = {"sea", "rail_land", "road", "air"}
STATUSES = {"observed", "estimated", "synthetic"}
KALININGRAD = {"kaliningrad_oblast", "kaliningrad_port"}
MAINLAND = {"mainland_russia", "ust_luga", "saint_petersburg"}


def valid_period(period: str, granularity: str) -> bool:
    pattern = r"[0-9]{4}-[0-9]{2}" if granularity == "month" else r"[0-9]{4}"
    if granularity not in {"month", "year"} or not re.fullmatch(pattern, period):
        return False
    try:
        date.fromisoformat(period + ("-01" if granularity == "month" else "-01-01"))
        return True
    except ValueError:
        return False


def validate(path: Path, *, registry: Path | None = None, root: Path = ROOT,
             corridor: bool = False, require_observations: bool = False,
             observed_only: bool = False) -> list[str]:
    if not path.is_file():
        return [f"Файл не найден: {path}"]
    errors, sources = [], None
    if registry is not None:
        try:
            sources = {a["source_id"]: a for a in load_registry(root, registry)}
            for artifact in sources.values():
                required = {"organization", "url", "downloaded_at", "local_path", "sha256",
                            "purpose", "period", "geography", "direction", "mode", "metric",
                            "original_unit", "limitations", "usage_rights"}
                if required - artifact.keys():
                    raise ValueError(f"Неполный паспорт: {artifact['source_id']}")
                verify_artifact(root, artifact)
        except (OSError, ValueError, KeyError) as error:
            return [f"Ошибка реестра источников: {error}"]
    seen, count = {}, 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            missing = REQUIRED - set(headers)
            if missing:
                return [f"Нет столбцов: {', '.join(sorted(missing))}"]
            if len(headers) != len(set(headers)):
                return ["Повторяющиеся названия столбцов"]
            for line, row in enumerate(reader, start=2):
                count += 1
                if None in row or any(value is None for value in row.values()):
                    errors.append(f"Строка {line}: число ячеек не совпадает с заголовком")
                    continue
                if not valid_period(row["period"], row["period_granularity"]):
                    errors.append(f"Строка {line}: неверный календарный период или шаг времени")
                if not all(row[field].strip() for field in ("origin", "destination", "cargo_group", "source_id")):
                    errors.append(f"Строка {line}: не заполнены OD, группа или источник")
                if row["mode"] not in MODES or row["status"] not in STATUSES or row["direction"] not in {"inbound", "outbound"}:
                    errors.append(f"Строка {line}: неизвестный вид, статус или направление")
                if observed_only and row["status"] != "observed":
                    errors.append(f"Строка {line}: для цели допустимы только observed")
                if row["value_tonnes"].strip():
                    try:
                        number = Decimal(row["value_tonnes"])
                        if not number.is_finite() or number < 0:
                            raise ValueError()
                    except (InvalidOperation, ValueError):
                        errors.append(f"Строка {line}: масса должна быть конечной и неотрицательной")
                if sources is not None and row["source_id"] not in sources:
                    errors.append(f"Строка {line}: source_id отсутствует в реестре")
                if corridor:
                    origin, destination = row["origin"], row["destination"]
                    expected = ("inbound" if origin in MAINLAND and destination in KALININGRAD else
                                "outbound" if origin in KALININGRAD and destination in MAINLAND else None)
                    if expected is None or expected != row["direction"]:
                        errors.append(f"Строка {line}: OD не соответствует коридору или направлению")
                ferry = row.get("in_wagon_on_ferry", "").lower()
                if ferry not in {"", "true", "false"} or (ferry == "true" and row["mode"] != "sea"):
                    errors.append(f"Строка {line}: вагон на пароме должен учитываться в sea")
                key = tuple(row.get(field, "") for field in (
                    "period", "period_granularity", "origin", "destination", "direction", "mode", "cargo_group", "route_id"))
                if key in seen:
                    errors.append(f"Строка {line}: повтор или конфликт со строкой {seen[key]}; требуется разбор, суммирование запрещено")
                else:
                    seen[key] = line
                if "original_value" in row and "original_unit" in row:
                    try:
                        converted = mass(row["original_value"], row["original_unit"])
                        expected_factor = {"тонн": "1", "тыс. тонн": "1000", "млн тонн": "1000000"}.get(row["original_unit"])
                        if converted == "" or row["value_tonnes"] == "":
                            if converted != row["value_tonnes"]:
                                raise ValueError("Пропуск заменён числом")
                        elif Decimal(converted) != Decimal(row["value_tonnes"]):
                            raise ValueError("Не совпадает пересчёт массы")
                        if "conversion_factor" in row and row["conversion_factor"] != expected_factor:
                            raise ValueError("Неверный коэффициент пересчёта")
                    except (ValueError, InvalidOperation) as error:
                        errors.append(f"Строка {line}: ошибка пересчёта: {error}")
    except (OSError, UnicodeError, csv.Error) as error:
        errors.append(f"Не удалось прочитать CSV: {error}")
    if count == 0 and require_observations:
        errors.append("Набор пуст: наблюдения не найдены; заголовок не является готовым датасетом")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corridor", action="store_true")
    parser.add_argument("--require-observations", action="store_true")
    parser.add_argument("--observed-only", action="store_true")
    args = parser.parse_args()
    issues = validate(args.path, registry=args.registry, root=args.root.resolve(), corridor=args.corridor,
                      require_observations=args.require_observations, observed_only=args.observed_only)
    if issues:
        print("\n".join(issues))
    else:
        with args.path.open(encoding="utf-8-sig", newline="") as stream:
            count = sum(1 for _ in csv.DictReader(stream))
        print(f"Проверки пройдены, строк: {count}. Выбор цели и методология требуют решения команды.")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
