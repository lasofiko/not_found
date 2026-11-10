"""Проверка снимка данных и выделение двух годовых рядов узкой линии."""
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import tomllib

from pipeline.common import ROOT, digest, load_registry
from pipeline.validate import validate
from pipeline.verify import verify

DIRECTIONS = ("inbound", "outbound")
MODEL_IDS = ("naive", "drift", "ses")


@dataclass(frozen=True)
class Series:
    direction: str
    years: tuple[int, ...]
    values: tuple[float, ...]
    origin: str
    destination: str
    cargo_group: str
    source_ids: tuple[str, ...]
    source_locators: tuple[str, ...]


def load_config(path: Path | None = None) -> dict:
    config = tomllib.loads((path or ROOT / "ml/config.toml").read_text(encoding="utf-8"))
    target, evaluation = config["target"], config["evaluation"]
    expected = {"route_id": "ust_luga_kaliningrad_ferry", "unit": "tonnes", "granularity": "year",
                "mode": "sea", "first_historical_year": 2015, "last_historical_year": 2023,
                "forecast_end_year": 2030, "scope_status": "experimental_line_demo"}
    if any(target.get(k) != v for k, v in expected.items()) or target.get("directions") != list(DIRECTIONS):
        raise ValueError("Конфигурация не соответствует годовой демонстрации двух направлений")
    if (evaluation.get("models") != list(MODEL_IDS) or evaluation.get("tie_break_order") != list(MODEL_IDS)
            or evaluation.get("development_years") != [2020, 2021]
            or evaluation.get("holdout_years") != [2022, 2023]
            or evaluation.get("selection_metric") != "MAE" or evaluation.get("baseline") != "naive"
            or evaluation.get("metrics") != ["MAE", "WAPE"] or evaluation.get("strategy") != "rolling_origin"):
        raise ValueError("Изменён зафиксированный протокол моделей, выбора или временной проверки")
    if config["forecast"] != {"interval_levels": [80, 95], "nonnegative": True}:
        raise ValueError("Изменены правила интервалов или неотрицательности")
    return config


def parse_series(rows: list[dict], config: dict) -> dict[str, Series]:
    target = config["target"]
    years = tuple(range(target["first_historical_year"], target["last_historical_year"] + 1))
    grouped = {direction: [] for direction in DIRECTIONS}
    if len(rows) != len(years) * len(DIRECTIONS):
        raise ValueError("Ожидаются ровно 18 годовых строк: по девять на каждое направление")
    for row in rows:
        direction = row.get("direction")
        if direction not in grouped:
            raise ValueError("Неизвестное направление целевого ряда")
        od = ("ust_luga", "kaliningrad_port") if direction == "inbound" else ("kaliningrad_port", "ust_luga")
        if (row.get("period_granularity") != "year" or row.get("mode") != "sea"
                or row.get("route_id") != target["route_id"] or row.get("status") != "observed"
                or (row.get("origin"), row.get("destination")) != od
                or row.get("in_wagon_on_ferry", "") not in {"", "true", "false"}):
            raise ValueError("Нужны наблюдённые годовые тонны указанной морской линии и OD")
        if not all(row.get(k) for k in ("cargo_group", "source_id", "source_locator")):
            raise ValueError("Не заполнены группа груза или происхождение наблюдения")
        try:
            year, value = int(row["period"]), Decimal(row["value_tonnes"])
            if str(year) != row["period"] or not value.is_finite() or value < 0 or not math.isfinite(float(value)):
                raise ValueError()
        except (KeyError, ValueError, InvalidOperation, OverflowError) as error:
            raise ValueError("Неверный год, пропущенная, отрицательная или неконечная масса") from error
        grouped[direction].append((year, float(value), row))
    result = {}
    for direction, items in grouped.items():
        items.sort(key=lambda x: x[0])
        if tuple(x[0] for x in items) != years:
            raise ValueError("В годовом ряде пропуск, повтор или неподдерживаемый год")
        if len({x[2]["cargo_group"] for x in items}) != 1:
            raise ValueError("В одном ряде нельзя смешивать разные группы груза")
        first = items[0][2]
        result[direction] = Series(direction, years, tuple(x[1] for x in items), first["origin"], first["destination"],
                                   first["cargo_group"], tuple(x[2]["source_id"] for x in items),
                                   tuple(x[2]["source_locator"] for x in items))
    return result


def load_dataset(root: Path, config: dict):
    root = root.resolve()
    errors = verify(root)
    if errors:
        raise ValueError("Комплект данных не прошёл проверку:\n" + "\n".join(errors[:10]))
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    relative = "data/processed/candidate_observations.csv"
    if relative not in manifest["outputs"] or manifest["candidate_rows"] != 18:
        raise ValueError("Нет заполненного ряда из 18 наблюдений; получите оригинал и пересоберите данные")
    path = root / relative
    provided = manifest.get("build_scope") == "provided_processed"
    errors = validate(path, registry=None if provided else root / "data/source_registry.json", root=root, corridor=True,
                      require_observations=True, observed_only=True, source_ids={a["source_id"] for a in manifest["inputs"]})
    if errors:
        raise ValueError("Целевой CSV не прошёл проверку:\n" + "\n".join(errors))
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if any(row.get("data_version") != manifest["data_version"] for row in rows):
        raise ValueError("Версия целевых строк не соответствует манифесту")
    series = parse_series(rows, config)
    used = {sid for s in series.values() for sid in s.source_ids}
    sources = [{k: a.get(k) for k in ("source_id", "organization", "url", "local_path", "sha256", "downloaded_at", "limitations")}
               for a in load_registry(root) if a["source_id"] in used]
    return series, {"data_version": manifest["data_version"], "dataset_path": relative,
                    "dataset_sha256": digest(path.read_bytes()), "sources": sources,
                    "data_build_scope": manifest.get("build_scope", "full"),
                    "verification_scope": manifest.get("verification_scope", "originals"),
                    "original_source_validation": manifest.get("original_source_validation", "performed"),
                    "source_bundle": manifest.get("source_bundle"),
                    "human_methodology_review": "pending"}
