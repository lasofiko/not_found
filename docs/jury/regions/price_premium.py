"""Ценовая надбавка трёх регионов к среднему по России: оценка по протоколу и прогноз.

Запуск из корня репозитория (комплект данных распакован):
    python docs/jury/regions/price_premium.py --root . --out reports/generated/price-premium-NEW
Правила — price-premium-protocol.md. Только стандартная библиотека.
Проверка ретроспективная по доступной версии данных; исторические даты публикаций неизвестны.
"""

import argparse
import csv
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

SOURCE = "data/processed/not_found_additions/open_data_2026-11-10/emiss_fixed_basket_monthly_wide.csv"
REGIONS = {
    "kaliningrad_39": "Калининградская область",
    "kamchatka_41": "Камчатский край",
    "primorye_25": "Приморский край",
}
METHODS = ("naive", "seasonal_naive", "drift", "ses")
WINDOWS = {"selection": ("2023-10", "2024-09"), "holdout": ("2024-10", "2025-09"), "final": ("2025-10", "2026-09")}
HORIZONS = (1, 3, 12)
FORECAST_END = "2027-12"
INTERVAL_FROM = "2021-01"
ALPHAS = [round(0.05 * k, 2) for k in range(1, 20)]
OUTPUTS = ("metrics.csv", "forecast.csv", "history.csv")
EVALUATION_STATUS = "retrospective_available_vintage"
LIMITATIONS = [
    "Исторические даты доступности и версии публикаций не подтверждены; "
    "это ретроспективная проверка доступной версии данных, не имитация real-time.",
    "Уточнённые значения 2025 года используются в доступной выгрузке; "
    "их исходные исторические версии не восстановлены.",
    "Итоговое окно не является слепой проверкой: значения находились в одном входном файле.",
    "Надбавка фиксированного набора не равна цене доставки и не доказывает причинное влияние транспорта.",
    "Знаменатель меняет территориальный охват с января 2023 года; возможен сдвиг уровня.",
    "Эмпирическое покрытие интервалов не проверено; ошибки относятся к указанным ретроспективным окнам, "
    "не гарантируют будущую точность.",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def result_passport(root: Path, out: Path, input_sha256: str) -> dict:
    """Паспорт нового запуска; не приписывает исторические даты доступности входу."""
    if sha256(root / SOURCE) != input_sha256:
        raise ValueError("Вход изменился во время исследования. Сохранённый результат нельзя публиковать.")
    implementation = Path(__file__)
    protocol = implementation.with_name("price-premium-protocol.md")
    settings = {
        "windows": WINDOWS,
        "methods": METHODS,
        "horizons": HORIZONS,
        "forecast_end": FORECAST_END,
        "interval_from": INTERVAL_FROM,
        "ses_alphas": ALPHAS,
        "selection_rule": "minimum_selection_MAE_h1_then_METHODS_order",
        "interval_rule": "empirical_error_quantiles_0.1_0.9_same_horizon_since_2021-01",
    }
    identity = {
        "input_sha256": input_sha256,
        "protocol_sha256": sha256(protocol),
        "implementation_sha256": sha256(implementation),
        "settings": settings,
    }
    version = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "result_version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance_status": "input_fingerprinted_at_run",
        "evaluation_status": EVALUATION_STATUS,
        "unit": "percentage_points",
        "source": {
            "organization": "Росстат, ЕМИСС",
            "indicator_id": "31052",
            "url": "https://www.fedstat.ru/indicator/31052",
            "declared_snapshot_date": "2026-10-12",
            "snapshot_date_basis": "Дата выгрузки из исходного протокола; "
            "не подтверждает даты исторических публикаций.",
            "publication_dates": None,
            "vintage_verified": False,
        },
        "input": {"path": SOURCE, "sha256": input_sha256},
        "protocol": {
            "path": "docs/jury/regions/price-premium-protocol.md",
            "sha256": identity["protocol_sha256"],
            **settings,
        },
        "implementation": {
            "path": "docs/jury/regions/price_premium.py",
            "sha256": identity["implementation_sha256"],
            "python_version": platform.python_version(),
        },
        "limitations": LIMITATIONS,
        "outputs": {name: sha256(out / name) for name in OUTPUTS},
    }


def idx(period: str) -> int:
    year, month = map(int, period.split("-"))
    return year * 12 + month - 1


def per(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


def load(root: Path) -> dict[str, dict[int, float]]:
    series = {region: {} for region in REGIONS}
    with (root / SOURCE).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            base = row["RU_to_2022"] or row["RU_excl_new_from_2023"]
            for region in REGIONS:
                if base and row[region]:
                    series[region][idx(row["period"])] = 100 * (float(row[region]) / float(base) - 1)
    return series


def ses_level(values: list[float], alpha: float) -> float:
    level = values[0]
    for value in values[1:]:
        level = alpha * value + (1 - alpha) * level
    return level


def ses_alpha(values: list[float]) -> float:
    best, best_error = ALPHAS[0], float("inf")
    for alpha in ALPHAS:
        level, error = values[0], 0.0
        for value in values[1:]:
            error += (value - level) ** 2
            level = alpha * value + (1 - alpha) * level
        if error < best_error:
            best, best_error = alpha, error
    return best


def predict(method: str, history: list[float], horizon: int) -> float | None:
    """Прогноз на `horizon` месяцев вперёд по истории без пропусков, заканчивающейся месяцем выпуска."""
    if method == "naive":
        return history[-1]
    if method == "seasonal_naive":
        back = 12 - (horizon - 1) % 12
        return history[-back] if len(history) >= back else None
    if method == "drift":
        return history[-1] + horizon * (history[-1] - history[0]) / (len(history) - 1) if len(history) > 1 else None
    return ses_level(history, ses_alpha(history))


def backtest(values: dict[int, float], first: int, last: int) -> list[dict]:
    start, rows = min(values), []
    for target in range(first, last + 1):
        for horizon in HORIZONS + tuple(h for h in range(1, 16) if h not in HORIZONS):
            origin = target - horizon
            history = [values[i] for i in range(start, origin + 1) if i in values]
            if target not in values or len(history) != origin - start + 1 or len(history) < 24:
                continue
            for method in METHODS:
                forecast = predict(method, history, horizon)
                if forecast is not None:
                    rows.append(
                        {
                            "target": target,
                            "horizon": horizon,
                            "method": method,
                            "forecast": forecast,
                            "actual": values[target],
                            "error": forecast - values[target],
                        }
                    )
    return rows


def mae(rows: list[dict], method: str, horizon: int, first: int, last: int) -> tuple[float | None, int]:
    errors = [
        abs(r["error"])
        for r in rows
        if r["method"] == method and r["horizon"] == horizon and first <= r["target"] <= last
    ]
    return (mean(errors) if errors else None), len(errors)


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("reports/generated/price-premium"))
    args = parser.parse_args()
    if any((args.out / name).exists() for name in OUTPUTS + ("passport.json",)):
        parser.error("Каталог уже содержит результат исследования. Укажите новый --out; прежние файлы сохраняются.")
    input_sha256 = sha256(args.root / SOURCE)
    series = load(args.root)
    metrics, forecasts, history_rows, summary = [], [], [], []
    for region, name in REGIONS.items():
        values = series[region]
        last_known = max(values)
        rows = backtest(values, idx(INTERVAL_FROM), last_known)
        table = {}
        for window, (first, last) in WINDOWS.items():
            for method in METHODS:
                for horizon in HORIZONS:
                    value, count = mae(rows, method, horizon, idx(first), idx(last))
                    table[window, method, horizon] = value
                    metrics.append(
                        {
                            "region": region,
                            "window": window,
                            "period": f"{first}..{last}",
                            "method": method,
                            "horizon_months": horizon,
                            "MAE_pp": value,
                            "observations": count,
                        }
                    )
        selected = min(METHODS, key=lambda m: (table["selection", m, 1], METHODS.index(m)))
        for row in metrics:
            if row["region"] == region:
                row["selected_method"] = selected
        history = [values[i] for i in range(min(values), last_known + 1)]
        for step in range(1, idx(FORECAST_END) - last_known + 1):
            point = predict(selected, history, step)
            errors = [r["error"] for r in rows if r["method"] == selected and r["horizon"] == step]
            forecasts.append(
                {
                    "region": region,
                    "period": per(last_known + step),
                    "horizon_months": step,
                    "value_pp": point,
                    "lower_80": point - quantile(errors, 0.9),
                    "upper_80": point - quantile(errors, 0.1),
                    "method": selected,
                    "status": "forecast",
                    "experimental": step > 12,
                }
            )
        history_rows += [
            {"region": region, "period": per(i), "value_pp": v, "status": "derived_from_observed"}
            for i, v in sorted(values.items())
        ]
        summary.append(
            (
                name,
                selected,
                values[idx("2015-01")],
                values[last_known],
                per(last_known),
                {(w, h): (table[w, selected, h], table[w, "naive", h]) for w in WINDOWS for h in HORIZONS},
                forecasts[-1],
            )
        )
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in (("metrics.csv", metrics), ("forecast.csv", forecasts), ("history.csv", history_rows)):
        with (args.out / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    passport = result_passport(args.root, args.out, input_sha256)
    (args.out / "passport.json").write_text(json.dumps(passport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Статус: ретроспективная проверка доступной версии данных; историческая доступность публикаций не подтверждена."
    )
    for name, selected, first, last, period, table, end in summary:
        print(
            f"{name}: метод {selected}; надбавка 2015-01 {first:.1f} п. п., {period} {last:.1f} п. п.; "
            f"прогноз {end['period']} {end['value_pp']:.1f} ({end['lower_80']:.1f}…{end['upper_80']:.1f})"
        )
        for (window, horizon), (chosen, naive) in table.items():
            print(f"   {window:9s} h={horizon:2d}: MAE {chosen:.2f} п. п.; naive {naive:.2f}")


if __name__ == "__main__":
    main()
