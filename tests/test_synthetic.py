"""Проверки временных границ и статистических правил генерации."""

import calendar

import pytest

from ml.synthetic_evaluation import crps, evaluate
from pipeline.synthetic_data import advance
from pipeline.synthetic_methods import METHODS, simulate


def monthly_item(n=43):
    periods = [advance("2022-06", i) for i in range(n)]
    rows = []
    for period in periods:
        publication = advance(period, 1)
        year, month = map(int, publication.split("-"))
        rows.append(
            {
                "period": period,
                "status": "estimated",
                "available_by": publication + f"-{calendar.monthrange(year, month)[1]:02d}",
            }
        )
    return {
        "series_id": "test_only",
        "template_only": False,
        "periods": periods,
        "rows": rows,
        "values": [100.0 + (i % 12) * 3 for i in range(n)],
    }


@pytest.mark.parametrize("method", METHODS)
def test_reproducible_and_nonnegative(method):
    history = [float(i % 13) for i in range(36)]
    first, diagnostics = simulate(history, method, 12, 100, 42)
    second, _ = simulate(history, method, 12, 100, 42)
    assert first == second
    assert all(value >= 0 for path in first for value in path)
    assert diagnostics["clipped_share"] > 0


def test_constant_history_does_not_invent_noise():
    paths, diagnostics = simulate([5.0] * 30, "block_increment_bootstrap", 12, 10, 42)
    assert paths == [[5.0] * 12] * 10
    assert diagnostics["residual_std"] == 0


def test_short_training_is_rejected():
    with pytest.raises(ValueError):
        simulate([10.0] * 9, "iid_increment_bootstrap", 7, 10, 42)


def test_crps_against_hand_calculation():
    assert crps([0, 2], 1) == pytest.approx(0.5)
    assert crps([3], 1) == 2


def test_publication_lag_and_holdout_are_respected():
    item = monthly_item()
    rows, metrics = evaluate(item, realizations=10)
    assert rows and metrics
    for row in rows:
        assert row["training_end"] < row["origin"] < row["period"]
        assert row["training_n"] >= 24
        assert row["effective_horizon"] == row["horizon"] + 1
        last = item["rows"][item["periods"].index(row["training_end"])]
        assert last["available_by"][:7] <= row["origin"]
    assert max(r["period"] for r in rows if r["split"] == "selection") < min(
        r["period"] for r in rows if r["split"] == "holdout"
    )


def test_future_outcome_does_not_change_earlier_prediction():
    item = monthly_item()
    first, _ = evaluate(item, realizations=10)
    item["values"][-1] = 1000000
    second, _ = evaluate(item, realizations=10)
    assert [r["predicted"] for r in first] == [r["predicted"] for r in second]
    assert first[-1]["actual"] != second[-1]["actual"]


def test_no_selection_for_only_six_eligible_targets():
    item = monthly_item(30)
    for row in item["rows"]:
        row.pop("available_by")
    rows, _ = evaluate(item, realizations=10)
    assert rows and {r["split"] for r in rows} == {"holdout"}
