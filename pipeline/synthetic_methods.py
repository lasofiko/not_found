"""Простые воспроизводимые ансамбли; существующие модели не вызываются."""

import math
import random
from statistics import mean

METHODS = ("iid_increment_bootstrap", "block_increment_bootstrap", "seasonal_residual_bootstrap")


def quantile(values, probability):
    values = sorted(values)
    index = (len(values) - 1) * probability
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def simulate(values, method, horizon, realizations, seed):
    if len(values) < 24 or not 1 <= horizon <= 24 or not 1 <= realizations <= 2000:
        raise ValueError("Нужно минимум 24 месяца; горизонт 1–24, реализаций 1–2000")
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("История содержит недопустимую массу")
    if method not in METHODS:
        raise ValueError("Неизвестный метод")
    lag = 12 if method == "seasonal_residual_bootstrap" else 1
    residuals = [values[i] - values[i - lag] for i in range(lag, len(values))][-24:]
    center = mean(residuals)
    residuals = [v - center for v in residuals]
    rng = random.Random(seed)
    paths, clipped = [], 0
    for _ in range(realizations):
        errors = []
        while len(errors) < horizon:
            block = 3 if method == "block_increment_bootstrap" else 1
            start = rng.randrange(len(residuals) - block + 1)
            errors.extend(residuals[start : start + block])
        path = []
        for i, error in enumerate(errors[:horizon]):
            if lag == 12:
                base = values[-12 + i] if i < 12 else path[i - 12]
            else:
                base = path[-1] if path else values[-1]
            value = base + error
            clipped += value < 0
            path.append(max(0.0, value))
        paths.append(path)
    return paths, {
        "residual_count": len(residuals),
        "removed_drift": center,
        "residual_std": math.sqrt(mean(v * v for v in residuals)),
        "block_length": 3 if method == "block_increment_bootstrap" else 1,
        "clipped_share": clipped / (realizations * horizon),
    }
