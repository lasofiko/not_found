"""Численные инварианты, пропуски и защита от будущих значений в новом опыте."""

import json

import numpy as np
import pytest

from ml import synthetic_methods
from ml.synthetic_evaluation import available_history, stable_seed
from ml.synthetic_methods import fit, simulate
from pipeline.synthetic import scenarios_config


def test_reproducibility_diversity_and_recursive_uncertainty():
    values = [100 + 10 * np.sin(i) + i for i in range(36)]
    state = fit("rw_bootstrap", values)
    paths = simulate(state, 12, 1000, 42)
    assert np.array_equal(paths, simulate(state, 12, 1000, 42))
    assert not np.array_equal(paths, simulate(state, 12, 1000, 43))
    assert np.isfinite(paths).all() and np.all(paths >= 0)
    # Для RW на лог-шкале накопление инноваций должно расширять разброс горизонта.
    variance = np.log1p(paths).var(0)
    assert variance[-1] > variance[0] * 6
    assert len({p.tobytes() for p in paths}) == 1000
    assert stable_seed(42, "a", "rw") == stable_seed(42, "a", "rw")
    assert stable_seed(42, "a", "rw") != stable_seed(42, "b", "rw")


def test_training_masks_publication_lag_and_does_not_compress_gaps():
    rows = [dict(period=f"2024-{i:02d}", value=str(i), available_by=f"2024-{i + 1:02d}-28") for i in range(1, 7)]
    rows[2]["available_by"] = "2025-01-01"
    train, issued = available_history(rows, "2024-07")
    assert issued == "2024-06-30"
    assert [r["period"] for r in train] == ["2024-04", "2024-05"]
    rows[-1]["value"] = "99999999"
    assert available_history(rows, "2024-07")[0] == train


def test_augmentation_generator_preserves_month_positions_and_causal_fill(monkeypatch):
    captured = []
    real_fit = synthetic_methods.fit

    def record_fit(method, values):
        captured.append(list(values))
        return real_fit(method, values)

    monkeypatch.setattr(synthetic_methods, "fit", record_fit)
    values = [100 + i for i in range(24)]
    values[8] = None
    prediction = synthetic_methods.augmentation_forecast(values, 1, augment=True, seed=42)
    assert len(captured[0]) == 24
    assert captured[0][8] == captured[0][7]
    assert captured[0][9] == values[9]
    assert np.isfinite(prediction).all()
    assert values[8] is None


def test_scenarios_require_explicit_parameters_and_reject_wrong_semantics(tmp_path):
    config = scenarios_config()
    assert config["optimistic"]["throughput_multiplier"] is None
    assert config["stress"]["throughput_multiplier"] is None
    path = tmp_path / "scenarios.json"
    # Только фикстура для проверки интерфейса явного пользовательского ввода.
    path.write_text(
        json.dumps(
            {
                "optimistic": {"throughput_multiplier": 1.1, "assumption_source": "Тестовый ввод"},
                "stress": {"throughput_multiplier": 0.8, "assumption_source": "Тестовый ввод"},
            }
        )
    )
    assert scenarios_config(path)["stress"]["throughput_multiplier"] == 0.8
    path.write_text(json.dumps({"stress": {"throughput_multiplier": 1.2, "assumption_source": "Тестовый ввод"}}))
    with pytest.raises(ValueError, match="stress"):
        scenarios_config(path)
    path.write_text(json.dumps({"optimistic": {"throughput_multiplier": 1.2, "assumption_source": None}}))
    with pytest.raises(ValueError, match="основание"):
        scenarios_config(path)
