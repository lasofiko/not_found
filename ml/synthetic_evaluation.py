"""Отдельная проверка синтетических ансамблей на несинтетических целях."""

import calendar
from collections import defaultdict
from statistics import mean

from pipeline.synthetic_data import index_period
from pipeline.synthetic_methods import METHODS, quantile, simulate


def crps(draws, actual):
    ordered = sorted(draws)
    n = len(ordered)
    return mean(abs(v - actual) for v in ordered) - sum((2 * i - n + 1) * v for i, v in enumerate(ordered)) / n**2


def evaluate(item, seed=42, realizations=200):
    if item["template_only"]:
        return [], []
    periods, values, rows = item["periods"], item["values"], item["rows"]
    holdout_start = periods[-6]
    selection_start = periods[max(24, len(periods) - 18)]
    output = []
    for target_index in range(24, len(periods)):
        if periods[target_index] < selection_start:
            continue
        for horizon in (1, 3):
            origin_index = target_index - horizon
            if origin_index < 23:
                continue
            origin = periods[origin_index]
            year, month = map(int, origin.split("-"))
            cutoff = origin + f"-{calendar.monthrange(year, month)[1]:02d}"
            usable = [
                i
                for i in range(origin_index + 1)
                if not rows[i].get("available_by") or rows[i]["available_by"] <= cutoff
            ]
            # Нельзя сжимать календарные пропуски: используется только доступный префикс.
            if usable != list(range(len(usable))) or len(usable) < 24:
                continue
            train = values[: len(usable)]
            training_end = periods[len(usable) - 1]
            actual_horizon = index_period(periods[target_index]) - index_period(training_end)
            split = "holdout" if periods[target_index] >= holdout_start else "selection"
            actual = values[target_index]
            common = {
                "series_id": item["series_id"],
                "split": split,
                "origin": origin,
                "training_end": training_end,
                "training_n": len(train),
                "period": periods[target_index],
                "horizon": horizon,
                "effective_horizon": actual_horizon,
                "actual": actual,
                "actual_status": rows[target_index]["status"],
                "unit": "tonnes",
                "availability_policy": "publication_aware"
                if rows[0].get("available_by")
                else "retrospective_final_snapshot",
            }
            for method in (*METHODS, "naive", "seasonal_naive"):
                if method in METHODS:
                    paths, _ = simulate(train, method, actual_horizon, realizations, seed + target_index * 10 + horizon)
                    draws = [path[-1] for path in paths]
                else:
                    point = train[-1] if method == "naive" else train[-12 + (actual_horizon - 1) % 12]
                    draws = [point]
                predicted = mean(draws)
                lower, upper = quantile(draws, 0.1), quantile(draws, 0.9)
                winkler = upper - lower + 10 * max(lower - actual, actual - upper, 0)
                output.append(
                    {
                        **common,
                        "method": method,
                        "predicted": predicted,
                        "absolute_error": abs(predicted - actual),
                        "crps": crps(draws, actual),
                        "lower80": lower,
                        "upper80": upper,
                        "covered80": int(lower <= actual <= upper),
                        "width80": upper - lower,
                        "winkler80": winkler,
                    }
                )
    groups = defaultdict(list)
    for row in output:
        groups[(row["method"], row["split"], row["horizon"])].append(row)
    metrics = []
    for (method, split, horizon), group in sorted(groups.items()):
        total = sum(r["actual"] for r in group)
        metrics.append(
            {
                "series_id": item["series_id"],
                "method": method,
                "split": split,
                "horizon": horizon,
                "n": len(group),
                "start": min(r["period"] for r in group),
                "end": max(r["period"] for r in group),
                "mae": mean(r["absolute_error"] for r in group),
                "wape_pct": 100 * sum(r["absolute_error"] for r in group) / total if total else "",
                "crps": mean(r["crps"] for r in group),
                "coverage80": mean(r["covered80"] for r in group),
                "width80": mean(r["width80"] for r in group),
                "winkler80": mean(r["winkler80"] for r in group),
                "unit": "tonnes",
                "selection_start_planned": selection_start,
                "holdout_start_planned": holdout_start,
                "availability_policy": group[0]["availability_policy"],
            }
        )
    return output, metrics
