"""Изолированные экспериментальные генераторы; рабочие модели не используются."""

import warnings

import numpy as np

GENERATOR_VERSION = "flocast-synthetic-0.1"
METHODS = ("rw_bootstrap", "rw_block_bootstrap", "ets_residual_bootstrap", "ar1_residual_bootstrap")
BLOCK_LENGTH = 3


def fit(method, values):
    y = np.asarray(values, dtype=float)
    if method not in METHODS or len(y) < 12 or not np.isfinite(y).all() or np.any(y < 0):
        raise ValueError("Нужны хотя бы 12 конечных неотрицательных значений и известный метод")
    z = np.log1p(y)
    state = {
        "generator_id": method,
        "version": GENERATOR_VERSION,
        "transform": "log1p",
        "history": z.tolist(),
        "observations": len(z),
        "parameter_uncertainty": "not_included",
    }
    residuals = np.diff(z)
    if method == "ets_residual_bootstrap":
        from statsmodels.tsa.holtwinters import SimpleExpSmoothing

        with warnings.catch_warnings(record=True) as caught:
            fitted = SimpleExpSmoothing(z, initialization_method="estimated").fit(optimized=True)
        if not fitted.mle_retvals.get("success", True):
            raise ValueError("ETS: оптимизация не сошлась")
        state.update(
            alpha=float(fitted.params["smoothing_level"]),
            level=float(fitted.level[-1]),
            fit_warnings=sorted({str(w.message) for w in caught}),
        )
        residuals = np.asarray(fitted.resid[1:])
    elif method == "ar1_residual_bootstrap":
        from statsmodels.tsa.arima.model import ARIMA

        center = float(z.mean())
        with warnings.catch_warnings(record=True) as caught:
            fitted = ARIMA(z - center, order=(1, 0, 0), trend="c", enforce_stationarity=True).fit(
                method_kwargs={"maxiter": 500}
            )
        if not fitted.mle_retvals.get("converged", True):
            raise ValueError("ARIMA(1,0,0): оптимизация не сошлась")
        pars = dict(zip(fitted.param_names, fitted.params, strict=True))
        state.update(
            mean=float(pars["const"]) + center,
            phi=float(pars["ar.L1"]),
            order=[1, 0, 0],
            fit_center_log=center,
            optimizer_maxiter=500,
            fit_warnings=sorted({str(w.message) for w in caught}),
        )
        residuals = np.asarray(fitted.resid[1:])
    state["residual_mean_removed"] = float(residuals.mean())
    residuals = residuals - residuals.mean()
    if len(residuals) < 8 or not np.isfinite(residuals).all():
        raise ValueError("Недостаточно ошибок обучения")
    state["residuals"] = residuals.tolist()
    state["block_length"] = BLOCK_LENGTH if method == "rw_block_bootstrap" else 1
    return state


def _innovations(state, count, simulations, rng):
    residuals = np.asarray(state["residuals"])
    block = state["block_length"]
    if block == 1:
        return rng.choice(residuals, (simulations, count), replace=True)
    # Круговые последовательные блоки сохраняют локальную зависимость ошибок.
    starts = rng.integers(0, len(residuals), size=(simulations, (count + block - 1) // block))
    indices = (starts[:, :, None] + np.arange(block)) % len(residuals)
    return residuals[indices].reshape(simulations, -1)[:, :count]


def simulate(state, count, simulations=100, seed=42, *, initial_log=None):
    if type(count) is not int or not 1 <= count <= 240 or not 1 <= simulations <= 10000:
        raise ValueError("Недопустимые горизонт или число реализаций")
    rng = np.random.default_rng(seed)
    innovations = _innovations(state, count, simulations, rng)
    method = state["generator_id"]
    last = np.full(simulations, state["history"][-1] if initial_log is None else initial_log)
    level = np.full(simulations, state.get("level", last[0]) if initial_log is None else initial_log)
    trajectories = np.empty((simulations, count))
    for h in range(count):
        if method == "ets_residual_bootstrap":
            z = level + innovations[:, h]
            level = level + state["alpha"] * innovations[:, h]
        elif method == "ar1_residual_bootstrap":
            z = state["mean"] + state["phi"] * (last - state["mean"]) + innovations[:, h]
        else:
            z = last + innovations[:, h]
        # Нижняя граница — физическая неотрицательность, без неизвестного потолка мощности.
        z = np.maximum(z, 0)
        if not np.isfinite(z).all() or np.any(z > 40):
            raise ValueError("Нестабильная экстраполяция: реализация исключена, обрезка верхнего хвоста запрещена")
        trajectories[:, h] = np.expm1(z)
        last = z
    return trajectories


def point_forecast(state, count):
    method = state["generator_id"]
    last = float(state["history"][-1])
    out = []
    for _ in range(count):
        if method == "ar1_residual_bootstrap":
            last = state["mean"] + state["phi"] * (last - state["mean"])
        elif method == "ets_residual_bootstrap":
            last = state["level"]
        out.append(float(np.expm1(max(0, last))))
    return out  # Траектория без инноваций; не обязательно среднее или медиана распределения.


def augmentation_forecast(values, steps, *, impute=False, augment=False, seed=42):
    """Фиксированная Ridge(1) на трёх лагах; отдельный исследовательский получатель."""
    y = np.asarray([np.nan if v is None else v for v in values], dtype=float)
    if impute:
        for i in range(1, len(y)):
            if np.isnan(y[i]) and np.isfinite(y[i - 1]):
                y[i] = y[i - 1]  # Только прошлое, без интерполяции через будущее.
    z = np.log1p(y)

    def windows(history):
        x, target = [], []
        for i in range(3, len(history)):
            if np.isfinite(history[i - 3 : i + 1]).all():
                x.append(history[i - 3 : i][::-1])
                target.append(history[i])
        return np.asarray(x), np.asarray(target)

    x, target = windows(z)
    if len(x) < 6 or not np.isfinite(z[-3:]).all():
        raise ValueError("Недостаточно полных исторических окон")
    center, scale = x.mean(0), x.std(0)
    scale[scale == 0] = 1
    weight = np.ones(len(x))
    if augment:
        # Генератор видит только прошлое этого среза. Псевдоистории не пришиваются к календарю фактов.
        generator_history = y.copy()
        for i in range(1, len(generator_history)):
            if not np.isfinite(generator_history[i]):
                generator_history[i] = generator_history[i - 1]
        # Пропуск не сжимает календарь. Для генератора это причинная estimated-вставка,
        # а получатель продолжает обучаться на исходных полных окнах.
        state = fit("rw_block_bootstrap", generator_history)
        paths = simulate(state, len(y) - 1, simulations=20, seed=seed, initial_log=float(z[0]))
        sx, sy = [], []
        for path in paths:
            xx, yy = windows(np.log1p(np.r_[y[0], path]))
            sx.extend(xx)
            sy.extend(yy)
        synthetic_weight = 0.5 * len(x) / len(sx)
        weight = np.r_[weight, np.full(len(sx), synthetic_weight)]
        x, target = np.vstack((x, sx)), np.r_[target, sy]
    design = np.column_stack((np.ones(len(x)), (x - center) / scale))
    penalty = np.diag([0.0, 1.0, 1.0, 1.0])
    coefficients = np.linalg.solve(design.T @ (weight[:, None] * design) + penalty, design.T @ (weight * target))
    history = list(z)
    result = []
    for _ in range(steps):
        features = (np.asarray(history[-3:][::-1]) - center) / scale
        value = max(0.0, float(np.r_[1.0, features] @ coefficients))
        if value > 40:
            raise ValueError("Ridge: нестабильный прогноз")
        history.append(value)
        result.append(float(np.expm1(value)))
    return result
