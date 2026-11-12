"""Проверенные входы новых ML-экспериментов без изменения ETL-комплекта."""
import calendar
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from pipeline.common import digest, load_registry, verify_artifact
from pipeline.extract import extract_freight
from pipeline.verify import verify

PORT = "Порт Калининград"
SERIES_FIELDS = ["period", "period_granularity", "port", "measurement_scope", "cargo_group",
                 "value_tonnes", "unit", "status", "source_role", "source_id", "source_locator",
                 "previous_source_id", "previous_source_locator", "formula", "available_by", "data_version"]


def month_index(period: str) -> int:
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("Месяц должен иметь формат YYYY-MM")
    year, month = map(int, period.split("-"))
    try:
        date(year, month, 1)
    except ValueError as error:
        raise ValueError("Некорректный календарный месяц: " + period) from error
    return year * 12 + month - 1


def month_period(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_end(period: str) -> str:
    month_index(period)
    year, month = map(int, period.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def source_available_by(artifact: dict) -> str:
    explicit = artifact.get("published_at")
    if explicit:
        return date.fromisoformat(explicit[:10]).isoformat()
    url = artifact.get("resolved_url") or artifact.get("url") or ""
    found = re.search(r"/uploads/(\d{4})/(\d{2})/", url)
    if not found:
        raise ValueError("Не установлена историческая доступность: " + artifact["source_id"])
    return month_end("-".join(found.groups()))


def verified_context(root: Path, required: list[str], *, data_root=None):
    from pipeline.generation import resolve_data_root
    data_root = data_root or resolve_data_root(root)
    errors = verify(root, data_root=data_root)
    if errors:
        raise ValueError("Комплект не прошёл проверку: " + "; ".join(errors[:5]))
    root = data_root
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    for name in required:
        if "data/processed/" + name not in manifest["outputs"]:
            raise ValueError("Таблица не включена в манифест: " + name)
    ids = {a["source_id"] for a in manifest["inputs"]}
    artifacts = {a["source_id"]: a for a in load_registry(root) if a["source_id"] in ids}
    return manifest, artifacts


def _mass(value) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError()
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Пропущенная/некорректная масса в накопительном ряде") from error
    return result


def derive_port_series(cumulative: list[dict], artifacts: dict, data_version: str) -> list[dict]:
    selected = {}
    for row in cumulative:
        if (row["port"] != PORT or row["measurement_scope"] != "port_total"
                or row["cargo_group"] != "Всего грузов" or row["period"][:4] not in {"2023", "2024", "2025"}):
            continue
        art = artifacts.get(row["source_id"])
        if art is None:
            raise ValueError("Источник портовой строки отсутствует во входах манифеста")
        year, month = map(int, row["period"].split("-"))
        publication_year = art.get("publication_year")
        expected = 2024 if year == 2023 else year
        if publication_year != expected:
            continue
        if (art.get("publication_month") != month or row["unit"] != "tonnes"
                or row["period_granularity"] != "year_to_date" or row["status"] != "observed"
                or row.get("data_version") != data_version):
            raise ValueError("Несопоставимый период, единица, версия или статус портового накопления")
        if row["period"] in selected:
            raise ValueError("Неоднозначная версия портового накопления: " + row["period"])
        _mass(row["value"])
        selected[row["period"]] = row
    expected_periods = [month_period(i) for i in range(month_index("2023-01"), month_index("2025-12") + 1)]
    if sorted(selected) != expected_periods:
        missing = sorted(set(expected_periods) - selected.keys())
        raise ValueError("Неполные 36 накоплений; пропуски: " + ", ".join(missing))
    result = []
    for period in expected_periods:
        current = selected[period]
        previous = selected.get(month_period(month_index(period) - 1)) if period[5:] != "01" else None
        after, before = _mass(current["value"]), _mass(previous["value"]) if previous else Decimal(0)
        delta = after - before
        if delta < 0:
            raise ValueError("Отрицательная месячная разность: " + period)
        ids = [current["source_id"]] + ([previous["source_id"]] if previous else [])
        result.append({"period": period, "period_granularity": "month", "port": PORT,
                       "measurement_scope": "port_total", "cargo_group": "Всего грузов",
                       "value_tonnes": format(delta, "f"), "unit": "tonnes", "status": "estimated",
                       "source_role": "comparative" if period.startswith("2023") else "primary",
                       "source_id": current["source_id"], "source_locator": current["source_locator"],
                       "previous_source_id": previous["source_id"] if previous else "",
                       "previous_source_locator": previous["source_locator"] if previous else "",
                       "formula": f"{after} - {before}" if previous else str(after),
                       "available_by": max(source_available_by(artifacts[sid]) for sid in ids),
                       "data_version": data_version})
    return result


def load_port_series(root: Path):
    root = Path(root).resolve()
    from pipeline.generation import resolve_data_root
    data_root = resolve_data_root(root)
    manifest, artifacts = verified_context(root, ["port_cabotage_cumulative.csv"], data_root=data_root)
    root = data_root
    path = root / "data/processed/port_cabotage_cumulative.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = derive_port_series(list(csv.DictReader(stream)), artifacts, manifest["data_version"])
    ids = sorted({r["source_id"] for r in rows} | {r["previous_source_id"] for r in rows if r["previous_source_id"]})
    sources = [artifacts[sid] for sid in ids]
    for source in sources:
        verify_artifact(root, source)
    return rows, {"data_version": manifest["data_version"], "input_sha256": digest(path.read_bytes()),
                  "sources": sources, "availability_method": "Конец месяца размещения из официального URL; точный день не установлен.",
                  "human_methodology_review": "pending"}


def load_extended_line(root: Path):
    from ml.data import load_config, load_dataset
    root = Path(root).resolve()
    original, provenance = load_dataset(root, load_config())
    from pipeline.generation import resolve_data_root
    root = resolve_data_root(root)
    source = provenance["sources"][0]
    path = verify_artifact(root, source)
    rows, _ = extract_freight(path.read_text(encoding="utf-8"), range(2011, 2024))
    result = {}
    for direction, label in (("inbound", "Выгружено (тонн)"), ("outbound", "Погружено (тонн)")):
        selected = [r for r in rows if r["label"] == label]
        years = [int(r["period"]) for r in selected]
        values = [float(r["value_tonnes"]) for r in selected]
        if years != list(range(2011, 2024)) or values[4:] != list(original[direction].values):
            raise ValueError("Расширенная история не согласуется с исходным целевым рядом")
        result[direction] = {"years": years, "values": values, "source_id": source["source_id"],
                             "source_locators": [r["source_locator"] for r in selected]}
    return result, provenance
