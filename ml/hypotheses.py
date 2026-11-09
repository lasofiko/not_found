"""Первая проверка транспортных гипотез на доступных данных.

Результаты описывают провозную возможность и условные диапазоны. Они не являются наблюдёнными эффектами:
данных о спросе, заявках и очередях в наборе нет.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from ml.experiment_io import fingerprint, finish_manifest
from pipeline.common import ROOT, write_csv

FLEET_PATH = Path(__file__).with_name("inputs") / "ferry_fleet_monthly.csv"
OUTPUT_DIR = "reports/generated/ml-hypotheses"
WEATHER_POINT = "Балтийск"
STRONG_WIND_KMH = (40, 50)
MAIN_FERRIES = (3, 4)
MONTH_DAYS = 30.4375
AMBAL_REPAIR_DAYS = 73  # 1 мая – 13 июля 2026, публикации Оборонлогистики
TERMINAL_DESIGN_TONNES_MONTH = 439_350  # PDF Росморпорта «Балтийск», октябрь 2026
TERMINAL_LOAD_TONNES_MONTH = 147_300  # тот же PDF
CROSSING_HOURS = (38, 45)
PORT_HOURS = (24, 36, 48)
PORT_CUT = (0.1, 0.2, 0.3)
CENTRAL_TURNAROUND = (45, 36, 0.2)
PANEL_FIELDS = [
    "period",
    "tonnes",
    "precision",
    "scope",
    "wider_scope",
    "lower_bound",
    "active_ferries",
    "tonnes_per_ferry",
    "wind_max_mean_kmh",
    "strong_wind_days_40",
    "strong_wind_days_50",
    "precipitation_mm",
    "in_main_sample",
    "fleet_quote",
    "fleet_review_status",
    "source_url",
]
REGRESSION_FIELDS = [
    "sample",
    "model",
    "observations",
    "ferries_coef_tonnes",
    "ferries_ci_low",
    "ferries_ci_high",
    "wind_coef_tonnes_per_kmh",
    "wind_ci_low",
    "wind_ci_high",
    "r_squared",
    "residual_std_tonnes",
]
RESULT_FIELDS = [
    "hypothesis_id",
    "hypothesis",
    "indicator",
    "low",
    "central",
    "high",
    "unit",
    "status",
    "evidence",
    "main_limitation",
]
INDIRECT = "косвенная оценка"
SCENARIO = "сценарный результат действия"


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Не найдена таблица {path}. Запускайте команду из распакованного комплекта данных.")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_fleet(path: Path = FLEET_PATH) -> dict[str, dict]:
    fleet = {}
    for row in _read(path):
        if row["period"] in fleet:
            raise ValueError(f"Повтор месяца в таблице флота: {row['period']}")
        fleet[row["period"]] = {**row, "active_ferries": int(row["active_ferries"])}
    return fleet


def weather_by_month(rows: list[dict], point: str = WEATHER_POINT) -> dict[str, dict]:
    days: dict[str, dict] = defaultdict(lambda: {"wind": [], "precipitation": []})
    for row in rows:
        if row["geography"] != point:
            continue
        key = {"wind_speed_10m_max": "wind", "precipitation_sum": "precipitation"}.get(row["metric"])
        if key:
            days[row["period"][:7]][key].append(float(row["value"]))
    result = {}
    for month, values in days.items():
        wind = values["wind"]
        result[month] = {
            "wind_max_mean_kmh": mean(wind),
            "precipitation_mm": sum(values["precipitation"]),
            **{f"strong_wind_days_{limit}": sum(v >= limit for v in wind) for limit in STRONG_WIND_KMH},
        }
    return result


def build_panel(freight: list[dict], fleet: dict[str, dict], weather: dict[str, dict]) -> list[dict]:
    panel = []
    for row in freight:
        if row["series_id"] != "ferry_monthly" or row["direction_scope"] != "both" or row["period"] not in fleet:
            continue
        period, count = row["period"], fleet[row["period"]]
        if period not in weather:
            raise ValueError(f"Нет погоды за месяц {period}")
        scope, tonnes = row["measurement_scope"], float(row["value"])
        panel.append(
            {
                "period": period,
                "tonnes": tonnes,
                "precision": row["precision"],
                "scope": scope,
                "wider_scope": "Антей" in scope or "Пионерск" in scope,
                "lower_bound": row["precision"] == "lower_bound",
                "active_ferries": count["active_ferries"],
                "tonnes_per_ferry": tonnes / count["active_ferries"],
                **weather[period],
                "in_main_sample": count["active_ferries"] in MAIN_FERRIES,
                "fleet_quote": count["quote"],
                "fleet_review_status": count["review_status"],
                "source_url": count["source_url"],
            }
        )
    return sorted(panel, key=lambda r: r["period"])


def fit_ols(panel: list[dict], with_wind: bool) -> dict:
    try:
        import numpy as np
        import statsmodels.api as sm
    except ImportError as error:
        raise RuntimeError("Установите ML-зависимости: python -m pip install -e .[ml]") from error
    columns = [[r["active_ferries"] for r in panel]] + ([[r["wind_max_mean_kmh"] for r in panel]] if with_wind else [])
    design = sm.add_constant(np.column_stack(columns).astype(float))
    fitted = sm.OLS(np.array([r["tonnes"] for r in panel], dtype=float), design).fit(cov_type="HC3")
    interval = fitted.conf_int()
    result = {
        "observations": len(panel),
        "ferries_coef_tonnes": float(fitted.params[1]),
        "ferries_ci_low": float(interval[1][0]),
        "ferries_ci_high": float(interval[1][1]),
        "wind_coef_tonnes_per_kmh": None,
        "wind_ci_low": None,
        "wind_ci_high": None,
        "r_squared": float(fitted.rsquared),
        "residual_std_tonnes": float(np.sqrt(fitted.mse_resid)),
    }
    if with_wind:
        result.update(
            {
                "wind_coef_tonnes_per_kmh": float(fitted.params[2]),
                "wind_ci_low": float(interval[2][0]),
                "wind_ci_high": float(interval[2][1]),
            }
        )
    return result


def regressions(panel: list[dict]) -> list[dict]:
    samples = {
        "main_3_4_ferries": [r for r in panel if r["in_main_sample"]],
        "strict_no_wider_scope_no_lower_bound": [
            r for r in panel if r["in_main_sample"] and not r["wider_scope"] and not r["lower_bound"]
        ],
    }
    rows = []
    for name, sample in samples.items():
        for model, with_wind in (("ferries", False), ("ferries_plus_wind", True)):
            rows.append({"sample": name, "model": model, **fit_ols(sample, with_wind)})
    return rows


def turnaround_gain(crossing: float, port: float, cut: float) -> float:
    """Относительный прирост рейсов при сокращении портового времени."""
    cycle = 2 * crossing + port
    return cycle / (cycle - cut * port) - 1


def turnaround_grid() -> list[tuple[int, int, float, float]]:
    return [(c, p, x, turnaround_gain(c, p, x)) for c in CROSSING_HOURS for p in PORT_HOURS for x in PORT_CUT]


def _quantile_range(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    return ordered[round(0.25 * (len(ordered) - 1))], ordered[round(0.75 * (len(ordered) - 1))]


def _terminal_peak(rows: list[dict]) -> tuple[float, str]:
    best = (0.0, "")
    for row in rows:
        period = row["period"]
        if (
            "Росморпорт" in row["operator"]
            and row["cargo_group"] == "Всего грузов"
            and row["measurement_scope"] == "operator"
            and "2023-01" <= period <= "2025-12"
            and row["value"]
        ):
            best = max(best, (float(row["value"]), period))
    return best


def _line_comparison(root: Path) -> dict | None:
    folders = sorted(
        (root / "reports/generated/ml-line-research").glob("*/metrics.json"), key=lambda p: p.stat().st_mtime
    )
    if not folders:
        return None
    import json

    return json.loads(folders[-1].read_text(encoding="utf-8"))["metrics"]["2015"]


def results(
    panel: list[dict], regression: list[dict], candidate: list[dict], terminal_rows: list[dict], line: dict | None
) -> list[dict]:
    main = next(r for r in regression if r["sample"] == "main_3_4_ferries" and r["model"] == "ferries_plus_wind")
    low, central, high = main["ferries_ci_low"], main["ferries_coef_tonnes"], main["ferries_ci_high"]
    four = [r["tonnes"] for r in panel if r["active_ferries"] == 4]
    base = mean(four)
    gains = [g for *_, g in turnaround_grid()]
    peak, peak_month = _terminal_peak(terminal_rows)
    annual = defaultdict(dict)
    for row in candidate:
        annual[int(row["period"])][row["direction"]] = float(row["value_tonnes"])
    gaps = [v["inbound"] - v["outbound"] for v in annual.values()]
    last = annual[max(annual)]
    repair = AMBAL_REPAIR_DAYS / MONTH_DAYS
    limit_demand = "Спрос сверх перевезённого неизвестен; связь по 3–4 паромам не доказывает эффект."
    rows = [
        {
            "hypothesis_id": "reserve_ferry",
            "hypothesis": "Резервный паром на время ремонтов",
            "indicator": "Тонн на дополнительный паром в месяц",
            "low": low,
            "central": central,
            "high": high,
            "unit": "т/мес",
            "status": INDIRECT,
            "evidence": f"Регрессия по {main['observations']} месяцам с учётом ветра",
            "main_limitation": limit_demand,
        },
        {
            "hypothesis_id": "reserve_ferry",
            "hypothesis": "Резервный паром на время ремонтов",
            "indicator": "Потенциал на ремонт «Амбала», 73 дня",
            "low": low * repair,
            "central": central * repair,
            "high": high * repair,
            "unit": "т",
            "status": SCENARIO,
            "evidence": "Коэффициент × длительность ремонта",
            "main_limitation": limit_demand,
        },
        {
            "hypothesis_id": "added_sea_capacity",
            "hypothesis": "Постоянное увеличение морской мощности",
            "indicator": "Один дополнительный паром в год",
            "low": low * 12,
            "central": central * 12,
            "high": high * 12,
            "unit": "т/год",
            "status": SCENARIO,
            "evidence": "Коэффициент × 12 месяцев",
            "main_limitation": limit_demand + " Расходов в расчёте нет.",
        },
        {
            "hypothesis_id": "port_turnaround",
            "hypothesis": "Сокращение портовой части оборота",
            "indicator": "Прирост рейсов",
            "low": 100 * min(gains),
            "central": 100 * turnaround_gain(*CENTRAL_TURNAROUND),
            "high": 100 * max(gains),
            "unit": "%",
            "status": SCENARIO,
            "evidence": "Формула цикла; переход 38–45 ч, порт 24–48 ч, сокращение 10–30%",
            "main_limitation": "Фактическое время в портах и устранимая доля неизвестны.",
        },
        {
            "hypothesis_id": "port_turnaround",
            "hypothesis": "Сокращение портовой части оборота",
            "indicator": "Прирост при 4 паромах",
            "low": base * min(gains),
            "central": base * turnaround_gain(*CENTRAL_TURNAROUND),
            "high": base * max(gains),
            "unit": "т/мес",
            "status": SCENARIO,
            "evidence": f"Средний месяц с 4 паромами: {base:,.0f} т".replace(",", " "),
            "main_limitation": "Условный диапазон; рейсы целочисленны.",
        },
        {
            "hypothesis_id": "terminal_speedup",
            "hypothesis": "Ускорение терминальной обработки",
            "indicator": "Загрузка терминала к проектной мощности",
            "low": 100 * TERMINAL_LOAD_TONNES_MONTH / TERMINAL_DESIGN_TONNES_MONTH,
            "central": None,
            "high": 100 * peak / TERMINAL_DESIGN_TONNES_MONTH,
            "unit": "%",
            "status": INDIRECT,
            "evidence": f"Снимок октября 2026 и пик {peak_month}",
            "main_limitation": "Проектная мощность не равна доступной; очереди и время обработки не наблюдаются.",
        },
        {
            "hypothesis_id": "backhaul_consolidation",
            "hypothesis": "Обратная загрузка через встречные партии",
            "indicator": "Разность ввоза и вывоза линии в год",
            "low": min(gaps),
            "central": last["inbound"] - last["outbound"],
            "high": max(gaps),
            "unit": "т/год",
            "status": INDIRECT,
            "evidence": f"Годовой ряд линии 2015–{max(annual)}; central — {max(annual)}",
            "main_limitation": "Верхняя граница свободной массы, не спрос; рейсовых ведомостей нет.",
        },
    ]
    if line:
        for direction, label in (("inbound", "ввоз"), ("outbound", "вывоз")):
            rows.append(
                {
                    "hypothesis_id": "separate_forecast",
                    "hypothesis": "Раздельный прогноз и резервирование",
                    "indicator": f"MAE раздельного минус MAE суммы с долями, {label}, 2022–2023",
                    "low": None,
                    "central": line["directional"][direction]["holdout"]["MAE"]
                    - line["aggregate"][direction]["holdout"]["MAE"],
                    "high": None,
                    "unit": "т",
                    "status": INDIRECT,
                    "evidence": "ml.line_experiment, история с 2015",
                    "main_limitation": "Две проверки на направление; эффект на заявки не проверяется.",
                }
            )
    else:
        rows.append(
            {
                "hypothesis_id": "separate_forecast",
                "hypothesis": "Раздельный прогноз и резервирование",
                "indicator": "Сравнение не запускалось",
                "low": None,
                "central": None,
                "high": None,
                "unit": "",
                "status": "не рассчитано",
                "evidence": "Сначала запустите ml.line_experiment",
                "main_limitation": "Нет отчёта сравнения.",
            }
        )
    rows.append(
        {
            "hypothesis_id": "phased_investment",
            "hypothesis": "Единовременные или поэтапные вложения",
            "indicator": "Не рассчитывается",
            "low": None,
            "central": None,
            "high": None,
            "unit": "",
            "status": "не рассчитано",
            "evidence": "Нет CAPEX, OPEX и сроков",
            "main_limitation": "Требуются подтверждённые стоимости.",
        }
    )
    return rows


def _number(value, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ")


def report_text(version: str, data_version: str, panel: list[dict], regression: list[dict], outcome: list[dict]) -> str:
    per_ferry = [r["tonnes_per_ferry"] for r in panel if r["in_main_sample"]]
    q1, q3 = _quantile_range(per_ferry)
    lines = [
        "# Первая проверка транспортных гипотез",
        "",
        f"Версия отчёта `{version}`, данные `{data_version}`.",
        "",
        "Все значения — косвенные оценки или условные результаты действий, а не наблюдённые эффекты. Спрос, заявки и очереди в данных отсутствуют, поэтому подтвердить или опровергнуть гипотезы этот расчёт не может.",
        "",
        "## Результаты",
        "",
        "| Гипотеза | Показатель | Низ | Центр | Верх | Единица | Статус | Основа | Главное ограничение |",
        "|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for r in outcome:
        digits = 1 if r["unit"] == "%" else 0
        lines.append(
            f"| {r['hypothesis']} | {r['indicator']} | {_number(r['low'], digits)} | {_number(r['central'], digits)} | {_number(r['high'], digits)} | {r['unit']} | {r['status']} | {r['evidence']} | {r['main_limitation']} |"
        )
    lines += [
        "",
        "## Связь тоннажа с флотом и ветром",
        "",
        f"Основная выборка — месяцы с 3 или 4 паромами: {len(per_ferry)} месяцев, медиана {_number(median(per_ferry))} т на паром, межквартильный диапазон {_number(q1)}–{_number(q3)} т. Месяцы с 5 паромами включают «Антей» и другой охват, поэтому в основной выборке их нет.",
        "",
        "| Выборка | Модель | Месяцев | Паром, т | 95% ДИ | Ветер, т на км/ч | 95% ДИ | R² |",
        "|---|---|---:|---:|---|---:|---|---:|",
    ]
    for r in regression:
        wind_ci = "—" if r["wind_ci_low"] is None else f"{_number(r['wind_ci_low'])}…{_number(r['wind_ci_high'])}"
        lines.append(
            f"| {r['sample']} | {r['model']} | {r['observations']} | {_number(r['ferries_coef_tonnes'])} | {_number(r['ferries_ci_low'])}…{_number(r['ferries_ci_high'])} | {_number(r['wind_coef_tonnes_per_kmh'])} | {wind_ci} | {r['r_squared']:.2f} |"
        )
    lines += [
        "",
        f"Ветер — среднемесячный максимум за сутки в реанализе Open-Meteo для точки «{WEATHER_POINT}». Это не наблюдение на судах и не данные о штормовых закрытиях. Волн, погоды по маршруту и в Усть-Луге в наборе нет.",
        "",
        "Число паромов извлечено из текстов публикаций шаблоном и записано в `ml/inputs/ferry_fleet_monthly.csv`; человеческая проверка таблицы не выполнена. Месяцы за май–июль и октябрь 2023 не вошли: число паромов в тексте не определено.",
        "",
        "Тоннаж с января по май 2025 включает линию на Пионерский. Часть значений — нижние границы; их влияние показано в строгой выборке.",
        "",
        "## Участие ИИ",
        "",
        "Модуль и расчёт подготовил ИИ. Интерпретацию и применимость должен подтвердить человек.",
    ]
    return "\n".join(lines) + "\n"


def run_hypotheses(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    processed = root / "data/processed"
    manifest_path = processed / "build_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            "Нет data/processed/build_manifest.json. Запускайте команду из распакованного комплекта данных."
        )
    import json

    data_version = json.loads(manifest_path.read_text(encoding="utf-8"))["data_version"]
    fleet = load_fleet()
    panel = build_panel(
        _read(processed / "supplemental_freight.csv"), fleet, weather_by_month(_read(processed / "weather_daily.csv"))
    )
    if not any(r["in_main_sample"] for r in panel):
        raise ValueError("Нет месяцев с 3–4 паромами для расчёта.")
    regression = regressions(panel)
    outcome = results(
        panel,
        regression,
        _read(processed / "candidate_observations.csv"),
        _read(processed / "port_cabotage_monthly.csv"),
        _line_comparison(root),
    )
    version, _ = fingerprint(
        "bb-hyp-", {"data_version": data_version, "fleet": fleet, "panel": panel}, [Path(__file__)]
    )
    output = root / OUTPUT_DIR / version
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "ferry_month_panel.csv", PANEL_FIELDS, panel)
    write_csv(output / "fleet_regression.csv", REGRESSION_FIELDS, regression)
    write_csv(output / "hypothesis_results.csv", RESULT_FIELDS, outcome)
    (output / "hypotheses-report.md").write_text(
        report_text(version, data_version, panel, regression, outcome), encoding="utf-8"
    )
    finish_manifest(
        output, {"report_version": version, "data_version": data_version, "status": "indirect_estimates_only"}
    )
    return {"output_dir": output, "version": version, "months": len(panel)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Первая проверка транспортных гипотез")
    parser.add_argument("--root", type=Path, default=ROOT)
    result = run_hypotheses(parser.parse_args().root)
    print(f"Месяцев в панели: {result['months']}. Отчёт: {result['output_dir'] / 'hypotheses-report.md'}")


if __name__ == "__main__":
    main()
