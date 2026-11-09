"""Проверка готового комплекта передачи без изменения результатов."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from datetime import date
from decimal import Decimal, InvalidOperation

from pipeline.common import ROOT, artifact_path, digest, load_registry, verify_artifact
from pipeline.extract import mass
from pipeline.validate import validate
from pipeline.target_audit import check_source, human_review


def verify_extended(root: Path, manifest: dict) -> list[str]:
    """Содержательные проверки единиц и календаря дополнительных рядов."""
    from pipeline.validate import valid_period
    errors = []
    names = {"airport_freight.csv", "rail_transit_monthly.csv", "rail_transit_cargo.csv", "weather_daily.csv",
             "regional_source_indicators.csv", "connectivity_routes.csv", "border_source_observations.csv",
             "documentary_observations.csv", "lithuania_transport_counts.csv"}
    for relative in manifest["outputs"]:
        name = Path(relative).name
        if name not in names:
            continue
        seen = set()
        with (root / relative).open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                raw, val = row["original_value"], row["value"]
                absent = raw in {"", "—", "–", "-", "***", "-77777777", "-99999999", "CD", "ND"}
                ambiguous = "ambiguous_thousands_separator" in row.get("quality_flags", "")
                try:
                    expected = Decimal(raw.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")) * Decimal(row["conversion_factor"]) if not absent and not ambiguous else None
                    correct = val == "" if expected is None else Decimal(val) == expected and Decimal(val).is_finite()
                except (InvalidOperation, ValueError):
                    correct = False
                if not correct:
                    errors.append(f"{name}: ошибочный пересчёт/пропуск: {row['source_locator']}")
                step, period = row["period_granularity"], row["period"]
                try:
                    calendar_valid = date.fromisoformat(period).isoformat() == period if step == "day" else valid_period(period, step)
                except ValueError:
                    calendar_valid = False
                if not calendar_valid:
                    errors.append(f"{name}: неверный календарный период")
                key = (row["source_id"], row["source_locator"])
                if key in seen:
                    errors.append(f"{name}: повтор исходной ячейки")
                seen.add(key)
                if name == "rail_transit_monthly.csv" and (row["direction_scope"] not in {"inbound", "outbound"} or row["unit"] != "tonnes"):
                    errors.append("БЖД: неподтверждённое направление или единица")
                if name == "airport_freight.csv" and (row["metric"] not in {"airport_cargo", "airport_mail"} or row["direction_scope"] != "both"):
                    errors.append("Аэропорт: смешаны пассажиры/OD/почта")
                if name == "border_source_observations.csv" and row["unit"] != "truck_crossings":
                    errors.append("Пересечения грузовиков не должны становиться тоннами")
    return errors


def verify_supplemental(root: Path, manifest: dict) -> list[str]:
    errors = []
    def read(name):
        path = "data/processed/" + name
        if path not in manifest["outputs"]:
            return []
        with (root / path).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    for filename in ("regional_indicators.csv", "supplemental_freight.csv", "port_cabotage_cumulative.csv"):
        for row in read(filename):
            raw, val = row["original_value"], row["value"]
            special = filename.startswith("regional") and raw in {"CD", "ND", "-77777777", "-99999999"}
            absent = special or raw in {"", "-", "–", "—"}
            try:
                correct = val == "" if absent else Decimal(val) == Decimal(raw.replace(",", ".")) * Decimal(row["conversion_factor"])
            except InvalidOperation:
                correct = False
            if not correct:
                errors.append(f"{filename}: неверный пересчёт/пропуск: {row['source_locator']}")
            if filename != "regional_indicators.csv" and val and Decimal(val) < 0:
                errors.append(f"{filename}: отрицательное значение")
            if filename == "supplemental_freight.csv":
                if row["direction_scope"] not in {"inbound", "outbound", "both", "unknown"}:
                    errors.append("Неизвестный охват направлений дополнительного ряда")
                if row["status"] == "estimated" and not row["formula"]:
                    errors.append("Оценка дополнительного ряда без формулы")
    regional = read("regional_indicators.csv")
    if regional and [r for r in regional if r["indicator_code"] in {"Y477110061", "Y477110290", "Y477110293"}] != read("regional_freight.csv"):
        errors.append("Грузовой региональный срез отличается от общего набора")
    cumulatives = read("port_cabotage_cumulative.csv")
    port_key = lambda r: tuple(r[f] for f in ("source_id", "period", "port", "operator", "cargo_group", "measurement_scope", "unit"))
    lookup = defaultdict(list)
    for row in cumulatives:
        lookup[port_key(row)].append(row)
    for row in read("port_cabotage_monthly.csv"):
        current = lookup[port_key(row)]
        month = int(row["period"][5:])
        previous = lookup[port_key({**row, "source_id": row["previous_source_id"], "period": f"{row['period'][:4]}-{month - 1:02d}"})] if month > 1 else []
        correct = len(current) == 1 and (month == 1 or len(previous) == 1)
        if correct:
            expected = Decimal(current[0]["value"]) - (Decimal(previous[0]["value"]) if previous else 0)
            correct = Decimal(row["value"]) == expected and expected >= 0
        if not correct or row["status"] != "estimated" or row["period_granularity"] != "month" or not row["formula"]:
            errors.append("Месячная разность SeaNews не соответствует накоплениям")
    return errors


def verify(root: Path) -> list[str]:
    try:
        artifacts = load_registry(root)
        manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
        errors = []
        input_ids = {a["source_id"] for a in manifest["inputs"]}
        partial = manifest.get("build_scope") == "available_only"
        selected = [a for a in artifacts if a["source_id"] in input_ids]
        for artifact in artifacts:
            if not partial or artifact["source_id"] in input_ids or artifact_path(root, artifact).is_file():
                verify_artifact(root, artifact)
        registry_sha = digest((root / "data/source_registry.json").read_bytes())
        if registry_sha != manifest["registry_sha256"]:
            errors.append("Реестр изменился после сборки: требуется повторная сборка")
        for path, expected in {**manifest["outputs"], **manifest["code"],
                               **manifest.get("review_inputs", {}), **manifest.get("handoff_assets", {})}.items():
            actual = (root / path).resolve()
            if not actual.is_relative_to(root.resolve()):
                raise ValueError("Путь из манифеста выходит за корень комплекта")
            if not actual.is_file() or digest(actual.read_bytes()) != expected:
                errors.append(f"Не совпадает SHA-256 результата или кода: {path}")
        inputs = sorted((a["source_id"], a["sha256"]) for a in (selected if partial else artifacts))
        expected_inputs = sorted((a["source_id"], a["sha256"]) for a in manifest["inputs"])
        if inputs != expected_inputs:
            errors.append("Входные оригиналы не совпадают с манифестом сборки")
        if partial and set(manifest["unavailable_source_ids"]) != {a["source_id"] for a in artifacts} - input_ids:
            errors.append("Список недоступных оригиналов не совпадает с реестром")
        if errors:
            return errors
        errors.extend(validate(root / "data/processed/candidate_observations.csv",
                               registry=root / "data/source_registry.json", root=root,
                               corridor=True, require_observations=bool(manifest["candidate_rows"]), observed_only=True,
                               source_ids=input_ids if partial else None))
        if "data/processed/target-source-check.csv" in manifest["outputs"]:
            with (root / "data/processed/candidate_observations.csv").open(encoding="utf-8-sig", newline="") as stream:
                candidate = list(csv.DictReader(stream))
            expected_checks, _ = check_source(root, candidate, selected)
            with (root / "data/processed/target-source-check.csv").open(encoding="utf-8-sig", newline="") as stream:
                saved_checks = list(csv.DictReader(stream))
            if saved_checks != [{k: str(v) for k, v in r.items()} for r in expected_checks]:
                errors.append("Построчная сверка кандидата не совпадает с оригиналом")
            quality = json.loads((root / "data/processed/target-quality.json").read_text(encoding="utf-8"))
            if quality["human_review"] != human_review(root, expected_checks):
                errors.append("Статус человеческой проверки не совпадает с журналом")
        source_ids = input_ids
        for relative in manifest["outputs"]:
            if not relative.endswith(".csv"):
                continue
            with (root / relative).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            expected_count = manifest["table_rows"].get(Path(relative).name)
            if expected_count is not None and len(rows) != expected_count:
                errors.append(f"{relative}: число строк не совпадает с манифестом")
            for row in rows:
                for key in ("source_id", "previous_source_id"):
                    if row.get(key) and row[key] not in source_ids:
                        errors.append(f"{relative}: источник не включён в сборку: {row[key]}")
                if any(s not in source_ids for s in filter(None, row.get("upstream_source_ids", "").split(";"))):
                    errors.append(f"{relative}: первоисточник не включён в сборку")
        errors.extend(verify_supplemental(root, manifest))
        errors.extend(verify_extended(root, manifest))
        with (root / "data/processed/context_observations.csv").open(encoding="utf-8-sig", newline="") as stream:
            seen = set()
            for line, row in enumerate(csv.DictReader(stream), 2):
                if row["source_id"] not in source_ids:
                    errors.append(f"Контекст, строка {line}: нет паспорта источника")
                if mass(row["original_value"], row["original_unit"]) != row["value_tonnes"]:
                    errors.append(f"Контекст, строка {line}: неверный пересчёт или пропуск")
                if row["direction"] or row["status"] != "observed":
                    errors.append(f"Контекст, строка {line}: недопустимое направление или статус")
                key = (row["series_id"], row["period"], row["source_id"])
                if key in seen:
                    errors.append(f"Контекст, строка {line}: повторный ключ")
                seen.add(key)
        for filename in ("scenario_parameters.csv", "factors.csv"):
            with (root / "data/processed" / filename).open(encoding="utf-8-sig", newline="") as stream:
                for line, row in enumerate(csv.DictReader(stream), 2):
                    if row["source_id"] not in source_ids:
                        errors.append(f"{filename}, строка {line}: нет паспорта источника")
        table_files = ["exports_clean.csv", "exports_kaliningrad.csv", "export_growth_checks.csv",
                       "transit_items_clean.csv", "transit_monthly_summary.csv"]
        tables = {}
        for filename in table_files:
            relative = "data/processed/" + filename
            if relative not in manifest["outputs"]:
                continue
            with (root / relative).open(encoding="utf-8-sig", newline="") as stream:
                tables[filename] = list(csv.DictReader(stream))
                if len(tables[filename]) != manifest["table_rows"][filename]:
                    errors.append(f"{filename}: число строк не совпадает с манифестом")
                for line, row in enumerate(tables[filename], 2):
                    if row["source_id"] not in source_ids:
                        errors.append(f"{filename}, строка {line}: нет паспорта источника")
                    if filename.startswith("exports_"):
                        if row["trade_value_thousand_usd"] == "":
                            correct = row["trade_value_usd"] == ""
                        else:
                            correct = Decimal(row["trade_value_usd"]) == Decimal(row["trade_value_thousand_usd"]) * 1000
                        if not correct:
                            errors.append(f"{filename}, строка {line}: неверный пересчёт тыс. USD")
                        if filename == "exports_kaliningrad.csv" and row["reporting_geography"] != "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ":
                            errors.append(f"{filename}, строка {line}: неверная география выборки")
                    if filename == "transit_items_clean.csv":
                        for prefix in ("item_gross", "item_net"):
                            kg, tonnes = row[prefix + "_kg"], row[prefix + "_tonnes"]
                            correct = tonnes == "" if kg == "" else Decimal(tonnes) == Decimal(kg) / 1000
                            if not correct:
                                errors.append(f"{filename}, строка {line}: неверный пересчёт {prefix}")
                        if row["registered_date"] and row["completed_date"]:
                            days = (date.fromisoformat(row["completed_date"]) - date.fromisoformat(row["registered_date"])).days
                            if str(days) != row["transit_days"] or days < 0:
                                errors.append(f"{filename}, строка {line}: неверная длительность транзита")
        if "exports_clean.csv" in tables:
            selected = [r for r in tables["exports_clean.csv"] if r["reporting_geography"] == "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ"]
            if selected != tables.get("exports_kaliningrad.csv"):
                errors.append("Выборка Калининградской области не совпадает с полной экспортной таблицей")
        if "transit_items_clean.csv" in tables:
            grouped = defaultdict(list)
            for row in tables["transit_items_clean.csv"]:
                key = (row["registered_date"][:7], row["origin_country_code"], row["destination_country_code"],
                       row["actual_mode"], row["commodity_code"][:4], row["source_id"])
                grouped[key].append(row)
            seen_summary = set()
            for row in tables.get("transit_monthly_summary.csv", []):
                key = tuple(row[f] for f in ("period", "origin_country_code", "destination_country_code", "actual_mode", "commodity_prefix", "source_id"))
                items = grouped.get(key, [])
                correct = key not in seen_summary and bool(items) and int(row["item_rows"]) == len(items)
                correct = correct and int(row["declarations"]) == len({r["declaration_id_hash"] for r in items})
                for kind in ("gross", "net"):
                    values = [Decimal(r[f"item_{kind}_tonnes"]) for r in items if r[f"item_{kind}_tonnes"] != ""]
                    actual = row[f"item_{kind}_sum_tonnes"]
                    correct = correct and int(row[f"known_{kind}_rows"]) == len(values)
                    correct = correct and (actual == "" if not values else actual != "" and Decimal(actual) == sum(values))
                if not correct:
                    errors.append("Месячная сводка транзита не совпадает с товарными записями")
                seen_summary.add(key)
            if seen_summary != set(grouped):
                errors.append("Месячная сводка транзита содержит другой набор групп")
        if manifest["target_selection"] != "pending":
            errors.append("Этот комплект не должен автоматически утверждать целевой ряд")
        return errors
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as error:
        return [f"Не удалось проверить комплект: {error}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = verify(args.root.resolve())
    print("\n".join(errors) if errors else "Комплект проверен в пределах входов манифеста: оригиналы, результаты, код, происхождение и единицы. Цель ожидает решения команды.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
