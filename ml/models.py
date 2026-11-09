"""Три простые модели и переносимое JSON-состояние без pickle."""
import math
from statistics import NormalDist
import warnings

MODEL_IDS = ("naive", "drift", "ses")


def validate_state(state: dict) -> None:
    if state.get("model_id") not in MODEL_IDS:
        raise ValueError("Неизвестный метод прогноза")
    if type(state.get("n_observations")) is not int or state["n_observations"] < 3:
        raise ValueError("Некорректный размер обучающей выборки модели")
    for name in ("last_value", "level", "drift_per_year", "alpha", "sigma2"):
        value = state.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Неконечный или отсутствующий параметр модели: " + name)
    if state["last_value"] < 0 or state["level"] < 0 or state["sigma2"] < 0 or not 0 <= state["alpha"] <= 1:
        raise ValueError("Недопустимый параметр массы, дисперсии или сглаживания")


def fit_model(model_id: str, values) -> dict:
    values = [float(v) for v in values]
    if model_id not in MODEL_IDS or len(values) < 3 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Для модели нужны минимум три конечных неотрицательных наблюдения")
    n = len(values)
    differences = [b - a for a, b in zip(values, values[1:])]
    drift, alpha, level, notes = 0.0, 0.0, values[-1], []
    if model_id == "naive":
        sigma2 = math.fsum(d * d for d in differences) / (n - 1)
    elif model_id == "drift":
        drift = (values[-1] - values[0]) / (n - 1)
        sigma2 = math.fsum((d - drift) ** 2 for d in differences) / (n - 2)
    elif max(values) == min(values):
        alpha, sigma2 = 0.5, 0.0
        notes.append("Постоянный ряд: параметр сглаживания не идентифицируется, используется 0,5.")
    else:
        try:
            from statsmodels.tsa.exponential_smoothing.ets import ETSModel
        except ImportError as error:
            raise ValueError('Установите ML-зависимости: pip install -e ".[ml]"') from error
        scale = max(max(values), 1.0)
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            fitted = ETSModel([v / scale for v in values], error="add", trend=None, seasonal=None,
                              initialization_method="known", initial_level=values[0] / scale).fit(disp=False)
        if not fitted.mle_retvals.get("converged", False):
            raise ValueError("Оптимизация SES не сошлась")
        alpha = float(dict(zip(fitted.model.param_names, fitted.params))["smoothing_level"])
        if captured:
            notes.append("Предупреждения библиотеки SES: " + ", ".join(sorted({w.category.__name__ for w in captured})))
        level, residuals = values[0], []
        for value in values[1:]:
            residuals.append(value - level)
            level = alpha * value + (1 - alpha) * level
        sigma2 = math.fsum(e * e for e in residuals) / (n - 2)
    if sigma2 == 0:
        notes.append("Нулевая историческая дисперсия не исключает будущие структурные изменения.")
    state = {"model_id": model_id, "n_observations": n, "last_value": values[-1], "level": level,
             "drift_per_year": drift, "alpha": alpha, "sigma2": sigma2, "warnings": notes}
    validate_state(state)
    return state


def predict(state: dict, horizons, *, intervals=True) -> list[dict]:
    validate_state(state)
    rows = []
    for h in horizons:
        if type(h) is not int or h < 1:
            raise ValueError("Глубина прогноза должна быть положительным целым числом")
        kind, variance = state["model_id"], state["sigma2"]
        if kind == "ses":
            point = state["level"]
            variance *= 1 + state["alpha"] ** 2 * (h - 1)
        else:
            point = state["last_value"] + h * state["drift_per_year"]
            variance *= h * (1 + h / (state["n_observations"] - 1)) if kind == "drift" else h
        if not math.isfinite(point) or not math.isfinite(variance):
            raise ValueError("Прогноз или дисперсия вышли за допустимый числовой диапазон")
        clipped, bounds = point < 0, {}
        if intervals:
            for coverage in (80, 95):
                radius = NormalDist().inv_cdf(0.5 + coverage / 200) * math.sqrt(variance)
                lower, upper = point - radius, point + radius
                clipped = clipped or lower < 0 or upper < 0
                bounds[str(coverage)] = {"lower": max(0.0, lower), "upper": max(0.0, upper)}
        rows.append({"horizon_years": h, "value": max(0.0, point), "interval": bounds, "nonnegative_clipped": clipped})
    return rows
