"""Зафиксированные временные окна, доступность и выбор до проверки."""
from pathlib import Path
import math
import tomllib

from ml.port_models import MODEL_IDS, MissingLagError, fit_port_model, forecast_values
from pipeline.model_inputs import month_end, month_index, month_period

PROTOCOL = {
    "protocol_id": "ml-port-evaluation-2026-11-10", "models": list(MODEL_IDS), "baseline": "naive",
    "selection_start": "2024-07", "selection_end": "2025-06",
    "holdout_start": "2025-07", "holdout_end": "2025-12",
    "training_start": "2022-06", "training_end": "2025-12", "horizons": [1, 3],
    "metrics": ["MAE", "WAPE"], "minimum_consecutive_months": 24,
    "minimum_window_fraction": .5, "simulations": 2000, "seed": 42,
}
BACKTEST_FIELDS = ["model_id", "stage", "issued_at", "horizon_months", "train_start", "train_end", "training_rows",
                   "period", "model_steps", "actual_tonnes", "predicted_tonnes", "error_tonnes",
                   "absolute_error_tonnes", "fit_status", "warning"]


def load_port_protocol(path=None):
    config = tomllib.loads((Path(path) if path else Path(__file__).with_name("port_protocol.toml")).read_text(encoding="utf-8"))
    if config != PROTOCOL:
        raise ValueError("Изменён зафиксированный протокол месячных моделей, окон или метрик")
    return config


def available_history(rows: list[dict], issued_at: str) -> list:
    return [float(r["value_tonnes"]) if r.get("value_tonnes") not in ("", None)
            and r["available_by"] and r["available_by"] <= issued_at else None for r in rows]


def _consecutive(history):
    best = run = 0
    for value in history:
        run = run + 1 if value is not None else 0
        best = max(best, run)
    return best


def _score(rows, expected):
    valid = [r for r in rows if r["fit_status"] == "ok"]
    publish = len(valid) >= math.ceil(expected / 2)
    denominator = math.fsum(r["actual_tonnes"] for r in valid)
    total_error = math.fsum(r["absolute_error_tonnes"] for r in valid)
    return {"MAE": total_error / len(valid) if publish else None,
            "WAPE": 100 * total_error / denominator if publish and denominator > 0 else None,
            "observations": len(valid), "expected_months": expected,
            "successful_folds": len(valid), "failed_folds": sum(r["fit_status"] == "failed" for r in rows),
            "skipped_months": sum(r["fit_status"].startswith("skipped") for r in rows),
            "publication_status": "available" if publish else "insufficient_coverage"}


def evaluate_port(rows: list[dict], *, fitter=fit_port_model):
    config = load_port_protocol()
    start = month_index(rows[0]["period"])
    if [month_index(r["period"]) for r in rows] != list(range(start, start + len(rows))):
        raise ValueError("Календарь ряда должен сохранять пропуски явными строками")
    cutoff = month_index(config["selection_start"]) - start
    eligible = _consecutive(available_history(rows[:cutoff], month_end("2024-06"))) >= 24
    methods = MODEL_IDS if eligible else ("naive",)
    output, metrics, selected = [], {}, "naive"
    for stage, first_period, last_period in (("development", "2024-07", "2025-06"), ("holdout", "2025-07", "2025-12")):
        begin, end = month_index(first_period), month_index(last_period)
        for horizon in config["horizons"]:
            for first in range(begin, end + 1, horizon):
                issued_at = month_end(month_period(first - 1))
                stop = max(0, min(len(rows), first - start))
                history = available_history(rows[:stop], issued_at)
                known = [i for i, value in enumerate(history) if value is not None]
                targets = list(range(first - start, first - start + horizon))
                for model_id in methods:
                    forecasts, warning, failure_status = None, "", "failed"
                    try:
                        forecasts = forecast_values(fitter(model_id, history), targets)
                    except MissingLagError as error:
                        warning, failure_status = str(error), "skipped_missing_lag"
                    except (ValueError, ArithmeticError, RuntimeError) as error:
                        warning = str(error)
                    for offset, target in enumerate(targets):
                        row = rows[target] if 0 <= target < len(rows) else {}
                        actual = float(row["value_tonnes"]) if row.get("value_tonnes") not in ("", None) else None
                        predicted = forecasts[offset] if forecasts is not None else None
                        status = "skipped_missing_actual" if actual is None else "ok" if predicted is not None else failure_status
                        error = predicted - actual if predicted is not None and actual is not None else None
                        output.append({"model_id": model_id, "stage": stage, "issued_at": issued_at,
                            "horizon_months": horizon, "train_start": month_period(start + known[0]) if known else "",
                            "train_end": month_period(start + known[-1]) if known else "", "training_rows": len(known),
                            "period": month_period(start + target), "model_steps": target - known[-1] if known else "",
                            "actual_tonnes": actual, "predicted_tonnes": predicted, "error_tonnes": error,
                            "absolute_error_tonnes": abs(error) if error is not None else None,
                            "fit_status": status, "warning": warning})
        for model_id in methods:
            metrics.setdefault(model_id, {})[stage] = {str(h): _score(
                [r for r in output if r["model_id"] == model_id and r["stage"] == stage and r["horizon_months"] == h],
                end - begin + 1) for h in config["horizons"]}
        if stage == "development" and eligible:
            candidates = [(metrics[name][stage]["1"]["MAE"], priority, name)
                          for priority, name in enumerate(methods)
                          if metrics[name][stage]["1"]["MAE"] is not None
                          and not any(r["fit_status"] == "failed" for r in output if r["model_id"] == name)]
            if not candidates:
                raise ValueError("Ни один метод не прошёл окно выбора")
            selected = min(candidates)[2]
    for name in metrics:
        metrics[name]["selection_status"] = "performed" if eligible else "not_performed_insufficient_history"
    return output, metrics, selected
