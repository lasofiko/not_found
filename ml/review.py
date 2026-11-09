"""Сводки для паспорта модели: сравнение с baseline, перелом 2022–2023, внешняя сверка."""
import csv
from pathlib import Path

BASELINE = "naive"
BREAK_YEARS = (2022, 2023)
TERMINAL_MATCH_PERCENT = 1.0
BREAK_FIELDS = ["direction", "period", "previous_actual_tonnes", "actual_tonnes", "change_percent", "model_id", "stage",
                "train_end", "predicted_tonnes", "error_tonnes", "error_percent"]
TERMINAL_FIELDS = ["period", "line_kind", "line_tonnes", "terminal_tonnes", "difference_tonnes", "difference_percent",
                   "terminal_source", "role"]
METRIC_DEFINITIONS = {
    "MAE": {"unit": "tonnes", "formula": "sum(|predicted - actual|) / n", "role": "выбор метода и основное сравнение"},
    "WAPE": {"unit": "percent", "formula": "100 * sum(|predicted - actual|) / sum(actual)",
             "zero_denominator": "При нулевой сумме фактов значение null, а не ноль.",
             "role": "относительный масштаб ошибки, в выборе метода не участвует"},
    "RMSE": {"included": False,
             "reason": "При двух проверках на этап RMSE определяется почти одной большой ошибкой и не меняет выбор; "
                       "ошибка каждой проверки доступна в backtest.csv."},
}
WINDOW_ROLES = {
    "training": "Настройка параметров внутри каждого обучающего окна.",
    "development": "2020–2021: сравнение и выбор метода. Не является независимым подтверждением.",
    "holdout": "2022–2023: отдельная проверка после выбора. Выбор по ней не меняется; по две проверки на направление.",
}
NOT_PUBLISHABLE = [
    "Значения 2025–2030 как подтверждённый прогноз: проверен только горизонт один год.",
    "Значения 2024–2030 как основание для решений: стартовая точка 2023 расходится со строкой терминала, "
    "причина не установлена.",
    "Утверждение, что выбранный метод точнее baseline: на направление есть только две независимые проверки.",
    "Интервалы как гарантированное покрытие: их калибровка на этом ряде не проверена.",
    "Этот ряд как прогноз всего коридора, других линий, железной дороги, автомобильных или авиационных перевозок.",
    "Разбивку по группам грузов, по месяцам или чистую массу груза: таких данных в цели нет.",
    "Прогноз как потребность в перевозке или как эффект транспортного действия.",
]


def _difference(first, second):
    return None if first is None or second is None else first - second


def baseline_comparison(metrics: dict, selected: dict) -> dict:
    result = {}
    for direction, models in metrics.items():
        chosen = selected[direction]["model_id"]
        base, picked = models[BASELINE], models[chosen]
        stages = {}
        for stage in ("development", "holdout"):
            stages[stage] = {"selected_MAE": picked[stage]["MAE"], "baseline_MAE": base[stage]["MAE"],
                             "difference_MAE_tonnes": _difference(picked[stage]["MAE"], base[stage]["MAE"]),
                             "selected_WAPE": picked[stage]["WAPE"], "baseline_WAPE": base[stage]["WAPE"],
                             "observations": picked[stage]["observations"]}
        available = {m: v["holdout"]["MAE"] for m, v in models.items() if v["holdout"]["MAE"] is not None}
        result[direction] = {"selected_model": chosen, "baseline_model": BASELINE,
                             "selected_is_baseline": chosen == BASELINE, **stages,
                             "lowest_holdout_MAE_model": min(available, key=available.get) if available else None,
                             "superiority_confirmed": False,
                             "note": "Лучший метод на holdout показан для сведения и в выборе не участвует."}
    return result


def break_review(series: dict, backtest: list[dict]) -> list[dict]:
    rows = []
    for record in backtest:
        year = record["period"]
        if year not in BREAK_YEARS or record["fit_status"] != "ok":
            continue
        values = dict(zip(series[record["direction"]].years, series[record["direction"]].values))
        previous, actual = values[year - 1], record["actual_tonnes"]
        rows.append({"direction": record["direction"], "period": year, "previous_actual_tonnes": previous,
                     "actual_tonnes": actual,
                     "change_percent": 100 * (actual - previous) / previous if previous else None,
                     "model_id": record["model_id"], "stage": record["stage"], "train_end": record["train_end"],
                     "predicted_tonnes": record["predicted_tonnes"], "error_tonnes": record["error_tonnes"],
                     "error_percent": 100 * record["error_tonnes"] / actual if actual else None})
    return rows


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _terminal_values(root: Path) -> dict[int, tuple[float, str]]:
    processed = root / "data/processed"
    values = {}
    for row in _read(processed / "context_observations.csv"):
        if row["series_id"] == "terminal_cabotage" and row["value_tonnes"]:
            values[int(row["period"])] = (float(row["value_tonnes"]), "context_observations.csv:terminal_cabotage")
    for row in _read(processed / "port_cabotage_cumulative.csv"):
        year = int(row["period"][:4])
        # Берётся публикация своего года; сравнительные колонки следующего года могут немного отличаться.
        if ("Росморпорт" in row["operator"] and row["cargo_group"] == "Всего грузов"
                and row["measurement_scope"] == "operator" and row["period"].endswith("-12") and row["value"]
                and row["source_id"].startswith(f"seanews_{year}_12") and year not in values):
            source = f"port_cabotage_cumulative.csv:Росморпорт, Всего грузов, декабрь {year}"
            values[year] = (float(row["value"]), source)
    return values


def terminal_check(root: Path, series: dict, forecasts: dict) -> list[dict]:
    """Сверка суммы двух направлений со строкой терминала. В обучении и выборе не участвует."""
    terminal = _terminal_values(Path(root))
    line = {}
    for year in next(iter(series.values())).years:
        line[year] = ("published_line", sum(dict(zip(s.years, s.values))[year] for s in series.values()))
    for year in {int(p["period"]) for r in forecasts.values() for p in r["predictions"]}:
        line[year] = ("forecast_sum", sum(p["value"] for r in forecasts.values() for p in r["predictions"]
                                          if int(p["period"]) == year))
    rows = []
    for year in sorted(set(line) & set(terminal)):
        kind, value = line[year]
        reference, source = terminal[year]
        rows.append({"period": year, "line_kind": kind, "line_tonnes": value, "terminal_tonnes": reference,
                     "difference_tonnes": value - reference,
                     "difference_percent": 100 * (value - reference) / reference,
                     "terminal_source": source, "role": "external_check_not_used_for_training_or_selection"})
    return rows
