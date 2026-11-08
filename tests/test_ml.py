"""Все числовые примеры синтетические; реальная точность оценивается только в ML-отчёте."""
import csv
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ml.data import DIRECTIONS, Series, load_config, load_dataset, parse_series
from ml.evaluation import evaluate, score
from ml.models import fit_model, predict
from ml.predict import forecast, load_artifact
from ml.run import run
from pipeline.build import build
from test_pipeline import test_registry as make_registry


def synthetic_series(values=None):
    values = values or tuple(float(x) for x in range(10, 19))
    return {d: Series(d, tuple(range(2015, 2024)), tuple(values),
                      "ust_luga" if d == "inbound" else "kaliningrad_port",
                      "kaliningrad_port" if d == "inbound" else "ust_luga", "Синтетические грузы",
                      ("synthetic_fixture",) * 9, tuple(f"test:{i}" for i in range(9))) for d in DIRECTIONS}


def synthetic_rows():
    rows = []
    for direction, data in synthetic_series().items():
        for year, value in zip(data.years, data.values):
            rows.append({"period": str(year), "period_granularity": "year", "origin": data.origin,
                         "destination": data.destination, "direction": direction, "mode": "sea",
                         "route_id": "ust_luga_kaliningrad_ferry", "cargo_group": data.cargo_group,
                         "value_tonnes": str(value), "status": "observed", "source_id": "synthetic_fixture",
                         "source_locator": "test:" + str(year), "in_wagon_on_ferry": ""})
    return rows


class DataTests(unittest.TestCase):
    def test_sorting_preserves_two_independent_series(self):
        result = parse_series(list(reversed(synthetic_rows())), load_config())
        self.assertEqual(set(result), {"inbound", "outbound"})
        self.assertEqual(result["inbound"].years, tuple(range(2015, 2024)))
        self.assertEqual(len(result["outbound"].values), 9)

    def test_missing_duplicate_estimated_wrong_od_and_unit_scope_are_rejected(self):
        changes = [{"period": "2020"}, {"period_granularity": "month"}, {"origin": "mainland_russia"},
                   {"status": "estimated"}, {"status": "synthetic"}, {"mode": "rail_land"},
                   {"route_id": "different_line"}, {"source_id": ""}, {"cargo_group": "different_group"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                rows = synthetic_rows()
                rows[0].update(change)
                parse_series(rows, load_config())
        with self.assertRaises(ValueError):
            parse_series(synthetic_rows()[:-1], load_config())

    def test_missing_negative_nonfinite_and_special_mass_are_rejected(self):
        for value in ("", "ND", "-77777777", "-1", "NaN", "Infinity", "1e999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rows = synthetic_rows()
                rows[0]["value_tonnes"] = value
                parse_series(rows, load_config())

    def test_manifest_tampering_stops_ml(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            make_registry(root)
            build(root)
            path = root / "data/processed/candidate_observations.csv"
            path.write_bytes(path.read_bytes().replace(b"123", b"124"))
            with self.assertRaisesRegex(ValueError, "проверку"):
                load_dataset(root, load_config())


class ModelTests(unittest.TestCase):
    def test_naive_and_drift_have_distinct_hand_computed_forecasts(self):
        values = [10, 13, 16]
        self.assertEqual(predict(fit_model("naive", values), [1, 2])[1]["value"], 16)
        self.assertEqual([r["value"] for r in predict(fit_model("drift", values), [1, 2])], [19, 22])

    def test_prediction_intervals_widen_and_nest(self):
        for model in ("naive", "drift"):
            rows = predict(fit_model(model, [10, 30, 20, 40, 25]), [1, 7])
            widths = []
            for row in rows:
                a, b = row["interval"]["80"], row["interval"]["95"]
                self.assertTrue(0 <= b["lower"] <= a["lower"] <= row["value"] <= a["upper"] <= b["upper"])
                widths.append(b["upper"] - b["lower"])
            self.assertGreater(widths[1], widths[0])

    def test_nonnegative_policy_applies_to_points_and_intervals(self):
        row = predict(fit_model("drift", [30, 20, 10]), [7])[0]
        self.assertEqual(row["value"], 0)
        self.assertTrue(row["nonnegative_clipped"])
        self.assertTrue(all(bounds["lower"] >= 0 and bounds["upper"] >= 0 for bounds in row["interval"].values()))

    def test_constant_ses_does_not_invent_variability(self):
        state = fit_model("ses", [12] * 5)
        self.assertEqual(state["level"], 12)
        self.assertEqual(state["sigma2"], 0)
        self.assertTrue(state["warnings"])

    @unittest.skipUnless(importlib.util.find_spec("statsmodels"), "Нужна группа зависимостей ml")
    def test_ses_matches_analytic_ets_forecast_and_variance(self):
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel
        import pandas as pd
        values = [10., 14., 12., 18., 15., 20.]
        state = fit_model("ses", values)
        scale = max(values)
        fitted = ETSModel(pd.Series([v / scale for v in values]), error="add", trend=None, seasonal=None,
                          initialization_method="known", initial_level=values[0] / scale).fit(disp=False)
        expected = float(fitted.forecast(1).iloc[0]) * scale
        self.assertAlmostEqual(predict(state, [1])[0]["value"], expected, places=8)
        exact = fitted.get_prediction(start=len(values), end=len(values) + 2, method="exact")
        variance = [float(v) * scale * scale for v in exact.var_pred_mean]
        for h in range(1, 4):
            # Подтверждение формы дисперсии независимым расчётом библиотеки;
            # здесь применяется та же MLE-оценка масштаба, что в ETSModel.
            formula = float(fitted.scale) * scale * scale * (1 + state["alpha"] ** 2 * (h - 1))
            self.assertAlmostEqual(variance[h - 1], formula, places=8)


class EvaluationTests(unittest.TestCase):
    def test_every_fit_receives_only_the_past(self):
        recorded = []
        def fitter(name, values):
            recorded.append(tuple(values))
            return fit_model("naive", values)
        rows, _, _ = evaluate(synthetic_series(), load_config(), fitter=fitter)
        self.assertEqual(len(rows), 24)
        for row, values in zip(rows, recorded):
            self.assertEqual(len(values), row["train_end"] - 2015 + 1)
            self.assertLess(max(values), row["actual_tonnes"])
            self.assertEqual(row["train_end"] + 1, row["period"])

    def test_holdout_changes_cannot_change_selection_or_development(self):
        config = load_config()
        def fitter(name, values):
            return fit_model("naive", values)
        first = synthetic_series()
        second = synthetic_series((10, 11, 12, 13, 14, 15, 16, 10000, 1))
        a, ma, sa = evaluate(first, config, fitter=fitter)
        b, mb, sb = evaluate(second, config, fitter=fitter)
        self.assertEqual(sa, sb)
        self.assertTrue(all(s["model_id"] == "naive" for s in sa.values()))
        self.assertEqual([r for r in a if r["stage"] == "development"], [r for r in b if r["stage"] == "development"])
        self.assertNotEqual(ma["inbound"]["naive"]["holdout"], mb["inbound"]["naive"]["holdout"])

    def test_failed_fold_is_visible_and_method_ineligible(self):
        def fitter(name, values):
            if name == "ses":
                raise ValueError("Синтетическая ошибка подгонки")
            return fit_model("naive", values)
        rows, metrics, chosen = evaluate(synthetic_series(), load_config(), fitter=fitter)
        self.assertEqual(metrics["inbound"]["ses"]["development"]["failed_folds"], 2)
        self.assertIsNone(metrics["inbound"]["ses"]["development"]["MAE"])
        self.assertEqual(chosen["inbound"]["model_id"], "naive")
        self.assertTrue(all(r["warning"] for r in rows if r["model_id"] == "ses"))

    def test_zero_denominator_wape_is_undefined(self):
        result = score([{"actual_tonnes": 0, "absolute_error_tonnes": 1, "fit_status": "ok"}])
        self.assertEqual(result["MAE"], 1)
        self.assertIsNone(result["WAPE"])


@unittest.skipUnless(importlib.util.find_spec("statsmodels"), "Нужна группа зависимостей ml")
class ArtifactTests(unittest.TestCase):
    def test_full_run_reload_repeat_and_corruption_detection(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            make_registry(root)
            build(root)
            result = run(root, plots=False)
            first = {d: forecast(d, root=root) for d in DIRECTIONS}
            for direction, response in first.items():
                self.assertEqual(response["status"], "forecast")
                self.assertEqual(response["forecast_origin"], 2023)
                self.assertEqual(response["predictions"][0]["period"], "2024")
                self.assertEqual(response["predictions"][-1]["horizon_years"], 7)
                self.assertEqual(len(response["predictions"]), 7)
                self.assertEqual(response, forecast(direction, root=root, model_path=result["model_path"]))
            before = (result["model_path"]).read_bytes()
            again = run(root, plots=False)
            self.assertEqual(result["model_version"], again["model_version"])
            self.assertEqual(before, result["model_path"].read_bytes())
            self.assertEqual(first, {d: forecast(d, root=root) for d in DIRECTIONS})
            with (result["output_dir"] / "backtest_predictions.csv").open(encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 24)
            for direction, year in (("both", 2030), ("inbound", 2023), ("outbound", 2031), ("inbound", True)):
                with self.subTest(direction=direction, year=year), self.assertRaises(ValueError):
                    forecast(direction, year, root=root)
            result["model_path"].write_bytes(before + b" ")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                forecast(root=root)

    def test_missing_model_and_pointer_escape_are_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "Модель отсутствует"):
                forecast(root=root)
            (root / "models").mkdir()
            (root / "models/latest.json").write_text(json.dumps({"model_path": "../outside.json"}))
            with self.assertRaisesRegex(ValueError, "выходит"):
                load_artifact(root)
