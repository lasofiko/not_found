"""Ridge и два baseline; переносимое состояние, рекурсия и условные интервалы."""
import math
from statistics import fmean

MODEL_IDS = ("naive", "seasonal_naive", "ridge_0.1", "ridge_1", "ridge_10")
FEATURE_NAMES = ("lag_1", "rolling_mean_3", "month_sin", "month_cos")


def features(history: list, index: int) -> list[float]:
    if index < 3 or any(v is None for v in history[index - 3:index]):
        raise ValueError("Для признаков нужны три доступных предыдущих месяца")
    angle = 2 * math.pi * (index % 12) / 12  # История начинается с января.
    return [history[index - 1], fmean(history[index - 3:index]), math.sin(angle), math.cos(angle)]


def fit_port_model(model_id: str, history: list) -> dict:
    if model_id not in MODEL_IDS or len(history) < 12:
        raise ValueError("Неизвестный метод или слишком короткая история")
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                              or not math.isfinite(v) or v < 0) for v in history):
        raise ValueError("Масса должна быть конечной неотрицательной или отсутствующей")
    known = [i for i, v in enumerate(history) if v is not None]
    if not known:
        raise ValueError("Нет доступных наблюдений")
    state = {"model_id": model_id, "history": list(history), "last_known_index": known[-1],
             "feature_names": list(FEATURE_NAMES) if model_id.startswith("ridge") else [],
             "observations": len(known)}
    if model_id.startswith("ridge"):
        import numpy as np
        indices = [i for i in known if i >= 3 and all(v is not None for v in history[i - 3:i])]
        if len(indices) < 6:
            raise ValueError("Недостаточно доступных обучающих окон для Ridge")
        x = np.array([features(history, i) for i in indices])
        y = np.array([history[i] for i in indices])
        center, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale == 0] = 1
        z, intercept = (x - center) / scale, float(y.mean())
        alpha = float(model_id.removeprefix("ridge_"))
        coefficients = np.linalg.solve(z.T @ z + alpha * np.eye(x.shape[1]), z.T @ (y - intercept))
        state.update(alpha=alpha, center=center.tolist(), scale=scale.tolist(),
                     coefficients=coefficients.tolist(), intercept=intercept, training_windows=len(indices))
        residuals = [history[i] - _point(state, history, i) for i in indices]
    else:
        lag = 1 if model_id == "naive" else 12
        residuals = [history[i] - history[i - lag] for i in known if i >= lag and history[i - lag] is not None]
    if not residuals:
        raise ValueError("Нет ошибок обучения для оценки неопределённости")
    mean = fmean(residuals)
    state["residuals"] = [float(r - mean) for r in residuals]
    return state


def _point(state: dict, history: list, index: int):
    kind = state["model_id"]
    if kind == "naive":
        candidates = [v for v in history[:index] if v is not None]
        return candidates[-1] if candidates else None
    if kind == "seasonal_naive":
        return history[index - 12] if index >= 12 else None
    x = features(history, index)
    return max(0.0, state["intercept"] + math.fsum(c * (v - mean) / scale for c, v, mean, scale
                in zip(state["coefficients"], x, state["center"], state["scale"])))


def forecast_values(state: dict, target_indices: list[int]) -> list[float]:
    if not target_indices or min(target_indices) <= state["last_known_index"]:
        raise ValueError("Целевой месяц должен следовать за последним доступным")
    history = list(state["history"][:state["last_known_index"] + 1])
    while len(history) <= max(target_indices):
        point = _point(state, history, len(history))
        if point is not None and not math.isfinite(point):
            raise ValueError("Прогноз вышел за числовой диапазон")
        history.append(point)
    values = [history[i] for i in target_indices]
    if any(v is None for v in values):
        raise ValueError("Для сезонного прогноза нет соответствующего месяца прошлого года")
    return [max(0.0, float(v)) for v in values]


def bootstrap_predictions(state: dict, count: int, *, simulations=2000, seed=42) -> list[dict]:
    import numpy as np
    if type(count) is not int or not 1 <= count <= 60 or simulations < 100:
        raise ValueError("Горизонт должен быть от 1 до 60 месяцев")
    start = state["last_known_index"] + 1
    indices = list(range(start, start + count))
    points = forecast_values(state, indices)
    rng = np.random.default_rng(seed)
    noise = rng.choice(state["residuals"], size=(simulations, count), replace=True)
    trajectories = np.zeros((simulations, count))
    for sim in range(simulations):
        history = list(state["history"][:start])
        for h in range(count):
            value = _point(state, history, len(history))
            if value is None:
                raise ValueError("Неполная сезонная история итоговой модели")
            value = max(0.0, value + float(noise[sim, h]))
            if not math.isfinite(value):
                raise ValueError("Симуляция вышла за числовой диапазон")
            history.append(value)
            trajectories[sim, h] = value
    rows = []
    for h, point in enumerate(points):
        bounds = {}
        for level, quantiles in (("80", (.1, .9)), ("95", (.025, .975))):
            lo, hi = np.quantile(trajectories[:, h], quantiles)
            bounds[level] = {"lower": min(float(lo), point), "upper": max(float(hi), point)}
        rows.append({"value": point, "interval": bounds})
    return rows
