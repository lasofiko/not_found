"""Отдельная проверка источников излома 2025; модели и слепые итоги не читаются."""
import argparse
import csv
import gzip
import json
from decimal import Decimal
from pathlib import Path

from pipeline.bundle_sources import sheets
from pipeline.common import ROOT, digest, load_registry, write_csv, write_json
from pipeline.supplemental import parse_port

PORT = "Порт Петропавловск-Камчатский"
TARGET = "petropavlovsk_total"
TOTAL = "Всего грузов"
GAS = "сжиженный газ"
TOLERANCE = Decimal("0.000001")  # Только шум представления Excel, в тоннах.
SAVED_SERIES = "reports/generated/ml-port/bb-port-6454be65293a/training_series.csv"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(left, right):
    return abs(Decimal(left) - Decimal(right)) <= TOLERANCE


def read_issue(root, artifact):
    """Ограничение года проверяется до обращения к байтам таблицы."""
    require(artifact.get("publication_year") in {2024, 2025}, "Запрещённый год выпуска для диагностики")
    tables = sheets(root, artifact)  # sheets проверяет SHA-256 оригинала.
    rows = parse_port(tables, artifact, "source_audit", target_ports={PORT}, max_year=2025)
    return tables, rows


def check_context(tables, row, year):
    """Проверка сырой ячейки и контекста отдельно от состояния parse_port."""
    sheet, cell = row["source_locator"].rsplit("!", 1)
    table = dict(tables)[sheet]
    number = int(cell.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    column = cell[:len(cell) - len(str(number))]
    header = next(c for n, c in sorted(table.items()) if n <= 10 and "Год" in c.values())
    require(column == "C" and " ".join(header[column].split()) == TOTAL and header["B"] == "Год",
            "Ошибка колонки итога")
    require(table[number]["B"] in {str(year), str(year)[2:]}, "Ошибка года исходной строки")
    labels = [c.get("A", "").strip() for n, c in sorted(table.items()) if n <= number and c.get("A", "").strip()]
    ports = [label for label in labels if label.startswith("Порт ")]
    require(ports and ports[-1] == PORT and labels[-1] == "Итого " + PORT, "Ошибка строки итога порта")
    preamble = [v.strip().casefold() for n, c in table.items() if n <= 8 for v in c.values()]
    require("каботаж" in preamble and "тыс. тонн" in preamble, "Ошибка вида плавания или единицы")
    require(close(Decimal(table[number][column]) * 1000, row["value"]), "Ошибка перевода единицы")
    return {"year_cell": sheet + "!B" + str(number), "port_label": ports[-1],
            "total_label": labels[-1], "column_header": " ".join(header[column].split()), "original_unit": "тыс. тонн"}


def regional_rows(path):
    periods = {"118 январь-декабрь": "annual", "125 январь-сентябрь": "year_to_date_09"}
    result = []
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        columns = next(reader)
        # Числа других лет не попадают в словарь или наблюдения диагностики.
        selected = {key: columns.index(key) for key in columns if not key.isdigit() or key in {"2024", "2025"}}
        for line, raw in enumerate(reader, 2):
            row = {key: raw[index] for key, index in selected.items()}
            if (row["Субъекты РФ"].strip() != "11300000 Камчатский край"
                    or row["Виды плавания"].strip() != "70 каботаж"
                    or row["Виды грузов"].strip() != "57 всего грузов" or row["Период"] not in periods):
                continue
            require(row["Единица измерения"] == "169 тысяча тонн", "Неизвестная единица ЕМИСС")
            operation = {"1 отправлено": "departure", "2 прибыло": "arrival"}.get(row["отправлено-прибыло"])
            require(operation is not None, "Неизвестная операция ЕМИСС")
            for year in ("2024", "2025"):
                result.append({"period": year, "period_basis": periods[row["Период"]], "operation": operation,
                    "geography": "Камчатский край", "navigation": "каботаж", "cargo_group": TOTAL,
                    "value_tonnes": format(Decimal(row[year].replace(",", ".")) * 1000, "f"),
                    "status": "observed", "unit": "tonnes", "original_unit": row["Единица измерения"],
                    "source_locator": f"CSV:row={line};column={year}"})
    require(len(result) == 8, "Неполный или неоднозначный региональный срез")
    return result


def audit(root=ROOT):
    root = Path(root).resolve()
    registry = load_registry(root)
    sources, issues = [], {}
    for year, month in [(2024, 12), *((2025, m) for m in range(5, 13))]:
        matches = [a for a in registry if a.get("publication_year") == year and a.get("publication_month") == month
                   and a.get("dataset_kind") in {"port_cabotage", "historical_port_cabotage"}]
        require(len(matches) == 1, f"Неоднозначный выпуск {year}-{month:02d}")
        artifact = matches[0]
        tables, rows = read_issue(root, artifact)
        issues[year, month] = artifact, tables, rows
        sources.append({k: artifact[k] for k in ("source_id", "local_path", "sha256", "url", "organization",
                                                "publication_year", "publication_month", "usage_rights")})
    current_path = root / "data/processed/port_protocol" / TARGET / "training_series.csv"
    saved_path = root / SAVED_SERIES
    training = {r["period"]: r for r in read_csv(current_path)}
    saved = {r["period"]: r for r in read_csv(saved_path)}
    totals, cargo, operators = [], [], []

    def group_rows(month):
        return {r["cargo_group"]: r for r in issues[2025, month][2]
                if r["period"] == f"2025-{month:02d}" and r["measurement_scope"] == "port_total"}

    for month in range(6, 13):
        artifact, tables, rows = issues[2025, month]
        before_artifact = issues[2025, month - 1][0]
        now, before = group_rows(month), group_rows(month - 1)
        row, previous = now[TOTAL], before[TOTAL]
        context = check_context(tables, row, 2025)
        previous_context = check_context(issues[2025, month - 1][1], previous, 2025)
        value = Decimal(row["value"]) - Decimal(previous["value"])
        require(value >= 0, "Отрицательная разность итога")
        for target in (training, saved):
            item = target[row["period"]]
            require(all(item[k] == v for k, v in {
                "source_id": artifact["source_id"], "source_locator": row["source_locator"],
                "previous_source_id": before_artifact["source_id"],
                "previous_source_locator": previous["source_locator"],
                "source_role": "primary", "status": "estimated"}.items()), "Несовпадение происхождения разности")
            require(close(item["value_tonnes"], value), "Несовпадение месячной разности с обучающим рядом")
            require(item["formula"] == f"{row['value']} - {previous['value']}", "Несовпадение формулы")
        operator_totals = [r for r in rows if r["period"] == row["period"]
                           and r["measurement_scope"] == "operator" and r["cargo_group"] == TOTAL]
        require(len({r["operator"] for r in operator_totals}) == len(operator_totals), "Повтор оператора")
        require(close(sum(Decimal(r["value"]) for r in operator_totals), row["value"]), "Нарушен баланс операторов")
        require(close(Decimal(now["сухогрузы"]["value"]) + Decimal(now["наливные грузы"]["value"]), row["value"]),
                "Нарушен баланс сухогрузов и налива")
        operator_gas = [r for r in rows if r["period"] == row["period"]
                        and r["measurement_scope"] == "operator" and r["cargo_group"] == GAS]
        require(close(sum(Decimal(r["value"]) for r in operator_gas), now[GAS]["value"]),
                "Нарушен баланс сжиженного газа по операторам")
        totals.append({"period": row["period"], **context, "source_id": artifact["source_id"],
            "sha256": artifact["sha256"], "source_locator": row["source_locator"], "cumulative_tonnes": row["value"],
            "previous_source_id": before_artifact["source_id"], "previous_sha256": before_artifact["sha256"],
            "previous_source_locator": previous["source_locator"], "previous_year_cell": previous_context["year_cell"],
            "previous_cumulative_tonnes": previous["value"], "monthly_tonnes": format(value, "f"),
            "formula": f"{row['value']} - {previous['value']}", "unit": "tonnes", "status": "estimated",
            "check": "matches_original_current_and_frozen_training", "operator_balance": "matches"})
        for group, item in now.items():
            prior = before.get(group)
            missing = not prior or item["value"] == "" or prior["value"] == ""
            difference = None if missing else Decimal(item["value"]) - Decimal(prior["value"])
            cargo.append({"period": row["period"], "cargo_group": group, "source_id": artifact["source_id"],
                "source_locator": item["source_locator"], "cumulative_tonnes": item["value"],
                "previous_source_id": before_artifact["source_id"],
                "previous_source_locator": prior["source_locator"] if prior else "",
                "monthly_tonnes": "" if difference is None else format(difference, "f"), "unit": "tonnes",
                "status": "estimated", "quality_flag": "missing_cumulative" if missing else
                "negative_difference" if difference < 0 else ""})
        for item in operator_totals:
            gas = next(r for r in rows if r["period"] == row["period"] and r["operator"] == item["operator"]
                       and r["cargo_group"] == GAS)
            operators.append({"period": row["period"], "operator": item["operator"],
                "source_id": artifact["source_id"], "source_locator": item["source_locator"],
                "cumulative_tonnes": item["value"], "gas_source_locator": gas["source_locator"],
                "gas_cumulative_tonnes": gas["value"], "unit": "tonnes", "status": "observed"})
    passport_path = root / "data/processed/freight_packages/tables/kamchatka_statistics/source_registry.csv"
    passport = next(r for r in read_csv(passport_path) if r.get("provided_source_id") == "nsedc_39233")
    regional_root = root / "data/raw/freight_packages/kamchatka_statistics"
    regional_paths = sorted(regional_root.rglob("nsedc_port_39233_data.csv.gz"))
    require(len(regional_paths) == 1, "Неоднозначный региональный оригинал")
    regional_path = regional_paths[0]
    require(digest(regional_path.read_bytes()) == passport["sha256"], "SHA-256 регионального оригинала не совпадает")
    regional = regional_rows(regional_path)
    regional_id = passport["source_id"]
    for row in regional:
        row["source_id"] = regional_id
    sources.append({"source_id": regional_id, "local_path": regional_path.relative_to(root).as_posix(),
                    **{k: passport[k] for k in ("sha256", "organization", "publication_url", "retrieved_at",
                                               "usage_rights", "origin_verification")}})
    october = next(r for r in totals if r["period"] == "2025-10")
    gas_october = next(r for r in cargo if r["period"] == "2025-10" and r["cargo_group"] == GAS)
    annual = group_rows(12)[TOTAL]
    for series in (training, saved):
        require(close(sum(Decimal(series[f"2025-{m:02d}"]["value_tonnes"]) for m in range(1, 13)), annual["value"]),
                "Сумма месячных разностей не замыкается на годовое накопление")
    comparative = next(r for r in issues[2025, 12][2] if r["period"] == "2024-12"
                       and r["measurement_scope"] == "port_total" and r["cargo_group"] == TOTAL)
    original_2024 = next(r for r in issues[2024, 12][2] if r["period"] == "2024-12"
                         and r["measurement_scope"] == "port_total" and r["cargo_group"] == TOTAL)
    check_context(issues[2025, 12][1], comparative, 2024)
    check_context(issues[2024, 12][1], original_2024, 2024)
    first_gas = next(r["period"] for r in cargo if r["cargo_group"] == GAS and Decimal(r["cumulative_tonnes"]) > 0)
    preceding_operators = {r["operator"] for r in operators if r["period"] < first_gas}
    new_gas_operators = [r["operator"] for r in operators if r["period"] == first_gas
                         and r["operator"] not in preceding_operators and Decimal(r["gas_cumulative_tonnes"]) > 0]
    summary = {"target_id": TARGET, "audited_through": "2025-12", "months_checked": len(totals),
        "annual_telescoping_check": "matches_current_and_frozen_training",
        "extraction_error_found": False, "first_gas_publication_period": first_gas, "cargo_group": GAS,
        "new_gas_operators": new_gas_operators,
        "october_total_monthly_tonnes": october["monthly_tonnes"],
        "october_gas_difference_tonnes": gas_october["monthly_tonnes"],
        "annual_total_tonnes": annual["value"], "annual_comparative_2024_tonnes": comparative["value"],
        "annual_original_2024_tonnes": original_2024["value"],
        "annual_comparative_2024_locator": comparative["source_locator"],
        "annual_original_2024_locator": original_2024["source_locator"],
        "annual_gas_tonnes": group_rows(12)[GAS]["value"],
        "limits": ["Первое включение накопления не доказывает физическую перевалку всего объёма за октябрь.",
                   "Региональное прибытие и портовая перевалка различаются по географии и операции.",
                   "НЦСЭД — зеркало ЕМИСС; нативный оригинал ведомства отдельно не сверялся.",
                   "Это машинная сверка, не запись о ручной приёмке Пети."],
        "post_2025_issues_read": False, "models_loaded": False, "blind_results_loaded": False}
    # Все проверки закончены до записи; таблицы обучения не изменяются.
    output = root / "reports/generated/petropavlovsk-audit-2025"
    for name, rows in (("cell_checks.csv", totals), ("cargo_differences.csv", cargo),
                       ("operators.csv", operators), ("regional_checks.csv", regional)):
        write_csv(output / name, list(rows[0]), rows)
    write_json(output / "sources.json", sources)
    write_json(output / "summary.json", summary)
    outputs = ["cell_checks.csv", "cargo_differences.csv", "operators.csv", "regional_checks.csv",
               "sources.json", "summary.json"]
    write_json(output / "audit_manifest.json", {"inputs": {s["local_path"]: s["sha256"] for s in sources},
        "audit_code_sha256": digest(Path(__file__).read_bytes()),
        "data_versions": {"current": training["2025-12"]["data_version"],
                          "frozen_training": saved["2025-12"]["data_version"]},
        "training_sha256": {str(p.relative_to(root)): digest(p.read_bytes()) for p in (current_path, saved_path)},
        "regional_passport_sha256": digest(passport_path.read_bytes()),
        "registry_sha256": digest((root / "data/source_registry.json").read_bytes()),
        "outputs": {name: digest((output / name).read_bytes()) for name in outputs}})
    return {"output": str(output.relative_to(root)), "months_checked": len(totals), "extraction_error_found": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(audit(args.root), ensure_ascii=False))
