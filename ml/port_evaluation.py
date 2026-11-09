"""Временная проверка с маской доступности источников на дату выдачи."""
from ml.evaluation import score
from ml.port_models import MODEL_IDS, fit_port_model, forecast_values
from pipeline.model_inputs import month_end, month_index, month_period

BACKTEST_FIELDS = ["model_id", "stage", "issued_at", "horizon_months", "train_end", "training_rows",
                   "period", "model_steps", "actual_tonnes", "predicted_tonnes", "error_tonnes",
                   "absolute_error_tonnes", "fit_status", "warning"]


def available_history(rows: list[dict], issued_at: str) -> list:
    # Не доступны ни обучающие цели, ни входы признаков из будущих публикаций.
    return [float(r["value_tonnes"]) if r["available_by"] <= issued_at else None for r in rows]


def evaluate_port(rows: list[dict], *, fitter=fit_port_model):
    start = month_index(rows[0]["period"])
    output, metrics = [], {}
    selected = None
    for stage, months in (("development", range(1, 7)), ("holdout", range(7, 13))):
        for horizon in (1, 3):
            origins = list(months) if horizon == 1 else list(months)[::3]
            for month in origins:
                first = month_index(f"2025-{month:02d}")
                issued_at = month_end(month_period(first - 1))
                history = available_history(rows[:first - start], issued_at)
                targets = list(range(first - start, first - start + horizon))
                for model_id in MODEL_IDS:
                    state, forecasts, warning = None, None, ""
                    try:
                        state = fitter(model_id, history)
                        forecasts = forecast_values(state, targets)
                    except (ValueError, ArithmeticError, RuntimeError) as error:
                        warning = str(error)
                    known = [i for i, v in enumerate(history) if v is not None]
                    for offset, target in enumerate(targets):
                        actual = float(rows[target]["value_tonnes"])
                        predicted = forecasts[offset] if forecasts is not None else None
                        error = predicted - actual if predicted is not None else None
                        output.append({"model_id": model_id, "stage": stage, "issued_at": issued_at,
                                       "horizon_months": horizon, "train_end": month_period(start + known[-1]) if known else "",
                                       "training_rows": len(known), "period": rows[target]["period"],
                                       "model_steps": target - known[-1] if known else "",
                                       "actual_tonnes": actual, "predicted_tonnes": predicted,
                                       "error_tonnes": error, "absolute_error_tonnes": abs(error) if error is not None else None,
                                       "fit_status": "ok" if predicted is not None else "failed", "warning": warning})
        for model_id in MODEL_IDS:
            metrics.setdefault(model_id, {})[stage] = {
                str(h): score([r for r in output if r["model_id"] == model_id and r["stage"] == stage
                               and r["horizon_months"] == h]) for h in (1, 3)}
        if stage == "development":
            candidates = [(metrics[name][stage]["1"]["MAE"], priority, name)
                          for priority, name in enumerate(MODEL_IDS) if metrics[name][stage]["1"]["MAE"] is not None]
            if not candidates:
                raise ValueError("Ни один метод не прошёл предварительную проверку")
            selected = min(candidates)[2]
    return output, metrics, selected
