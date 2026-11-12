"""Временная проверка отдельного опыта; 2026 год не открывается для оценки."""

from collections import defaultdict

import numpy as np

from ml.synthetic_methods import METHODS, augmentation_forecast, fit, point_forecast, simulate
from pipeline.model_inputs import month_end, month_index, month_period


def stable_seed(seed, *parts):
    import hashlib

    return int.from_bytes(hashlib.sha256((str(seed) + ":" + ":".join(map(str, parts))).encode()).digest()[:4], "big")


def metrics(truth, prediction):
    y, p = np.asarray(truth, float), np.asarray(prediction, float)
    e = y - p
    return {
        "n": len(y),
        "mae": float(np.abs(e).mean()),
        "rmse": float(np.sqrt((e**2).mean())),
        "wape": float(np.abs(e).sum() / np.abs(y).sum()) if np.abs(y).sum() else None,
    }


def available_history(rows, target):
    issued_at = month_end(month_period(month_index(target) - 1))
    known = [r for r in rows if r["period"] < target and r["available_by"] <= issued_at and r["value"] != ""]
    if not known:
        raise ValueError("Нет доступной истории")
    # Обучение не сжимает временные разрывы. Берётся последний непрерывный участок.
    tail = [known[-1]]
    for r in reversed(known[:-1]):
        if month_index(tail[-1]["period"]) - month_index(r["period"]) != 1:
            break
        tail.append(r)
    return list(reversed(tail)), issued_at


def backtest(history, seed):
    groups = defaultdict(list)
    for row in history:
        groups[row["series_id"]].append(row)
    predictions, results, selected = [], [], {}
    for series_id, rows in sorted(groups.items()):
        rows.sort(key=lambda r: r["period"])
        truth = {r["period"]: float(r["value"]) for r in rows}
        for phase, first, last in (("development", "2024-07", "2025-06"), ("holdout", "2025-07", "2025-12")):
            for horizon in (1, 3):
                for start in range(month_index(first), month_index(last) + 1, horizon):
                    target = month_period(start)
                    train, issued = available_history(rows, target)
                    last_known = month_index(train[-1]["period"])
                    steps = start - last_known + horizon - 1
                    y = [float(r["value"]) for r in train]
                    for method in ("naive", "seasonal_naive", *METHODS):
                        error = ""
                        point = paths = None
                        try:
                            if method == "naive":
                                point = [y[-1]] * steps
                            elif method == "seasonal_naive":
                                previous = {r["period"]: float(r["value"]) for r in train}
                                point = []
                                for step in range(1, steps + 1):
                                    period = month_period(last_known + step)
                                    value = previous[month_period(last_known + step - 12)]
                                    point.append(value)
                                    previous[period] = value
                            else:
                                state = fit(method, y)
                                point = point_forecast(state, steps)
                                paths = simulate(
                                    state,
                                    steps,
                                    simulations=1000,
                                    seed=stable_seed(seed, series_id, phase, target, horizon, method),
                                )
                        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as exc:
                            error = type(exc).__name__ + ": " + str(exc)
                        for h in range(horizon):
                            period = month_period(start + h)
                            index = start + h - last_known - 1
                            row = {
                                "series_id": series_id,
                                "phase": phase,
                                "horizon": horizon,
                                "first_target": target,
                                "period": period,
                                "issued_at": issued,
                                "training_start": train[0]["period"],
                                "training_end": train[-1]["period"],
                                "training_observations": len(train),
                                "model_steps": index + 1,
                                "method": method,
                                "truth": truth[period],
                                "truth_status": "estimated",
                                "prediction": point[index] if not error else None,
                                "lower80": None,
                                "upper80": None,
                                "lower95": None,
                                "upper95": None,
                                "error": error,
                                "unit": "tonnes",
                            }
                            if paths is not None and not error:
                                bounds = np.quantile(paths[:, index], [0.1, 0.9, 0.025, 0.975])
                                row.update(
                                    zip(("lower80", "upper80", "lower95", "upper95"), map(float, bounds), strict=True)
                                )
                            predictions.append(row)
                for method in ("naive", "seasonal_naive", *METHODS):
                    cell = [
                        r
                        for r in predictions
                        if r["series_id"] == series_id
                        and r["phase"] == phase
                        and r["horizon"] == horizon
                        and r["method"] == method
                    ]
                    good = [r for r in cell if not r["error"]]
                    result = {
                        "series_id": series_id,
                        "phase": phase,
                        "horizon": horizon,
                        "method": method,
                        "expected_n": len(cell),
                        "failures": len(cell) - len(good),
                        "n": 0,
                        "mae": None,
                        "rmse": None,
                        "wape": None,
                        "coverage80": None,
                        "coverage95": None,
                        "mean_width80": None,
                        "mean_width95": None,
                        "truth_status": "estimated",
                        "unit": "tonnes",
                        "selected_on_development": False,
                    }
                    if good:
                        result.update(metrics([r["truth"] for r in good], [r["prediction"] for r in good]))
                        if good[0]["lower80"] is not None:
                            for level in (80, 95):
                                result[f"coverage{level}"] = float(
                                    np.mean([r[f"lower{level}"] <= r["truth"] <= r[f"upper{level}"] for r in good])
                                )
                                result[f"mean_width{level}"] = float(
                                    np.mean([r[f"upper{level}"] - r[f"lower{level}"] for r in good])
                                )
                    results.append(result)
            if phase == "development":
                candidates = [
                    r
                    for r in results
                    if r["series_id"] == series_id
                    and r["phase"] == "development"
                    and r["horizon"] == 1
                    and r["method"] in METHODS
                    and not r["failures"]
                    and r["n"] == 12
                ]
                if not candidates:
                    raise ValueError("Нет генератора с полной development-проверкой: " + series_id)
                best = min(candidates, key=lambda r: (round(r["mae"], 6), METHODS.index(r["method"])))
                selected[series_id] = best["method"]
    for row in results:
        row["selected_on_development"] = row["method"] == selected[row["series_id"]]
    return results, predictions, selected


def augmentation_experiment(rail, seed):
    results, predictions, imputations = [], [], []
    for direction in ("inbound", "outbound"):
        data = sorted(
            [r for r in rail if r["direction_scope"] == direction and "2023-01" <= r["period"] <= "2024-12"],
            key=lambda r: r["period"],
        )
        if len(data) != 24 or any(r["unit"] != "tonnes" or r["status"] != "observed" for r in data):
            raise ValueError("Изменилось покрытие вспомогательного опыта транзита")
        for index in range(18, 24):
            train = [float(r["value"]) if r["value"] else None for r in data[:index]]
            target = data[index]
            if not target["value"]:
                continue
            for experiment in (
                "original_only",
                "causal_imputation",
                "synthetic_augmentation",
                "naive",
                "seasonal_naive",
            ):
                if experiment == "naive":
                    pred = next(v for v in reversed(train) if v is not None)
                elif experiment == "seasonal_naive":
                    pred = train[-12]
                    if pred is None:
                        continue
                else:
                    pred = augmentation_forecast(
                        train,
                        1,
                        impute=experiment == "causal_imputation",
                        augment=experiment == "synthetic_augmentation",
                        seed=stable_seed(seed, "augmentation", direction, target["period"]),
                    )[0]
                predictions.append(
                    {
                        "series_id": "rail_via_belarus_" + direction,
                        "experiment": experiment,
                        "period": target["period"],
                        "training_end": data[index - 1]["period"],
                        "truth": float(target["value"]),
                        "prediction": pred,
                        "truth_status": "observed",
                        "evaluation_status": "retrospective_available_vintage",
                        "unit": "tonnes",
                    }
                )
            for i, v in enumerate(train):
                if v is None:
                    estimate = next(value for value in reversed(train[:i]) if value is not None)
                    imputations.append(
                        {
                            "series_id": "rail_via_belarus_" + direction,
                            "fold": target["period"],
                            "period": data[i]["period"],
                            "value": estimate,
                            "status": "estimated",
                            "formula": "last_available_previous_value",
                            "source_id": data[i]["source_id"],
                            "reason": data[i]["quality_flags"],
                            "used_in": "causal_imputation_and_augmentation_generator_only",
                            "unit": "tonnes",
                        }
                    )
        for experiment in ("original_only", "causal_imputation", "synthetic_augmentation", "naive", "seasonal_naive"):
            group = [
                r
                for r in predictions
                if r["series_id"] == "rail_via_belarus_" + direction and r["experiment"] == experiment
            ]
            results.append(
                {
                    "series_id": "rail_via_belarus_" + direction,
                    "experiment": experiment,
                    **metrics([r["truth"] for r in group], [r["prediction"] for r in group]),
                    "evaluation_status": "retrospective_available_vintage",
                    "truth_status": "observed",
                    "unit": "tonnes",
                    "test_start": "2024-07",
                    "test_end": "2024-12",
                    "synthetic_total_weight": 0.5 if experiment == "synthetic_augmentation" else 0,
                    "human_review": "pending",
                    "expected_n": 6,
                    "unavailable_folds": 6 - len(group),
                }
            )
    return results, predictions, imputations
