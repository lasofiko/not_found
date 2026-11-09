"""Последовательная проверка с выбором только на предварительных срезах."""
import math

from ml.models import fit_model, predict

BACKTEST_FIELDS = ["direction", "model_id", "stage", "train_start", "train_end", "period", "horizon_years",
                   "actual_tonnes", "predicted_tonnes", "error_tonnes", "absolute_error_tonnes", "fit_status", "warning"]


def score(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["fit_status"] == "ok"]
    complete = bool(rows) and len(valid) == len(rows)
    denominator = math.fsum(r["actual_tonnes"] for r in valid)
    total_error = math.fsum(r["absolute_error_tonnes"] for r in valid)
    return {"MAE": total_error / len(valid) if complete else None,
            "WAPE": 100 * total_error / denominator if complete and denominator > 0 else None,
            "observations": len(rows), "successful_folds": len(valid), "failed_folds": len(rows) - len(valid)}


def evaluate(series: dict, config: dict, *, fitter=fit_model):
    rows, selected, metrics = [], {}, {}
    protocol = config["evaluation"]
    for direction, data in series.items():
        direction_rows = []
        for stage, years in (("development", protocol["development_years"]), ("holdout", protocol["holdout_years"])):
            for year in years:
                train = [v for y, v in zip(data.years, data.values) if y < year]
                train_years = [y for y in data.years if y < year]
                actual = data.values[data.years.index(year)]
                for model_id in protocol["models"]:
                    row = {"direction": direction, "model_id": model_id, "stage": stage,
                           "train_start": train_years[0], "train_end": train_years[-1], "period": year,
                           "horizon_years": 1, "actual_tonnes": actual, "predicted_tonnes": "",
                           "error_tonnes": "", "absolute_error_tonnes": "", "fit_status": "failed", "warning": ""}
                    try:
                        state = fitter(model_id, train)
                        point = predict(state, [1], intervals=False)[0]
                        error = point["value"] - actual
                        notes = list(state.get("warnings", []))
                        if point["nonnegative_clipped"]:
                            notes.append("Отрицательный точечный прогноз ограничен нулём.")
                        row.update(predicted_tonnes=point["value"], error_tonnes=error, absolute_error_tonnes=abs(error),
                                   fit_status="ok", warning=" ".join(notes))
                    except (ValueError, ArithmeticError, RuntimeError) as error:
                        row["warning"] = str(error)
                    direction_rows.append(row)
            if stage == "development":
                candidates = []
                for priority, model_id in enumerate(protocol["tie_break_order"]):
                    result = score([r for r in direction_rows if r["model_id"] == model_id])
                    if result["MAE"] is not None:
                        candidates.append((result["MAE"], priority, model_id))
                if not candidates:
                    raise ValueError("Ни один метод не прошёл все предварительные проверки: " + direction)
                mae, _, winner = min(candidates)
                selected[direction] = {"model_id": winner, "selection_stage": "development", "development_MAE": mae,
                                       "selection_years": list(protocol["development_years"])}
        metrics[direction] = {model_id: {stage: score([r for r in direction_rows if r["model_id"] == model_id
                                           and (stage == "all" or r["stage"] == stage)])
                                        for stage in ("development", "holdout", "all")}
                              for model_id in protocol["models"]}
        rows.extend(direction_rows)
    return rows, metrics, selected
