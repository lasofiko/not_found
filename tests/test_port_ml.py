"""Синтетические проверки происхождения, доступности, рекурсии и переноса."""
from copy import deepcopy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from ml.line_experiment import evaluate_line_experiment
from ml.port_evaluation import available_history, evaluate_port
from ml.port_models import features, fit_port_model, forecast_values, bootstrap_predictions
from ml.port_predict import forecast_port
from ml.port_run import run_port
from pipeline.common import write_json
from pipeline.model_inputs import derive_port_series, month_index, month_period, month_end, source_available_by


def cumulative_fixture():
    rows, artifacts = [], {}
    for year in (2023, 2024, 2025):
        for month in range(1, 13):
            # 2023 и 2024 имеют общий источник, но разные столбцы.
            pubyear = max(2024, year)
            sid = f"test_{pubyear}_{month:02d}"
            upload = month_period(month_index(f"{pubyear}-{month:02d}") + 1)
            artifacts[sid] = {"source_id": sid, "publication_year": pubyear, "publication_month": month,
                              "url": "https://example.test/uploads/" + upload.replace("-", "/") + "/file.xls"}
            rows.append({"period": f"{year}-{month:02d}", "period_granularity": "year_to_date",
                         "port": "Порт Калининград", "measurement_scope": "port_total", "cargo_group": "Всего грузов",
                         "value": str(month * (100 + (year - 2023) * 10)), "unit": "tonnes", "status": "observed",
                         "source_id": sid, "source_locator": f"sheet!C{year}{month}", "data_version": "synthetic"})
    return rows, artifacts


def monthly_fixture():
    cumulative, artifacts = cumulative_fixture()
    template = derive_port_series(cumulative, artifacts, "synthetic")[0]
    rows = []
    for i in range(43):
        period = month_period(month_index("2022-06") + i)
        rows.append({**template, "period": period, "cargo_group": "грузы на паромах",
            "value_tonnes": str(100 + i * 2 + 10 * math.sin(i / 2)),
            "available_by": month_end(month_period(month_index(period) + 1)),
            "source_role": "primary", "source_id": "synthetic_" + period,
            "target_id": "kaliningrad_ferries", "quality_flags": ""})
    return rows


class PortInputsTests(unittest.TestCase):
    def test_36_months_reset_in_january_and_keep_two_sources_and_roles(self):
        raw, arts = cumulative_fixture()
        rows = derive_port_series(raw, arts, "synthetic")
        self.assertEqual(len(rows), 36)
        self.assertEqual(rows[12]["value_tonnes"], "110")  # Не разность с декабрём другого года.
        self.assertEqual(rows[0]["source_role"], "comparative")
        self.assertEqual(rows[12]["source_role"], "primary")
        self.assertTrue(all(r["status"] == "estimated" for r in rows))
        self.assertEqual(rows[1]["previous_source_id"], rows[0]["source_id"])
        self.assertEqual(rows[11]["available_by"], "2025-01-31")
        self.assertEqual(rows[-1]["available_by"], "2026-01-31")

    def test_missing_duplicate_negative_wrong_unit_and_unknown_availability_stop_preparation(self):
        for mode in ("missing", "duplicate", "negative", "unit", "availability"):
            raw, arts = cumulative_fixture()
            if mode == "missing":
                raw.pop(4)
            elif mode == "duplicate":
                raw.append(deepcopy(raw[4]))
            elif mode == "negative":
                raw[5]["value"] = "0"
            elif mode == "unit":
                raw[4]["unit"] = "vehicles"
            else:
                arts[raw[4]["source_id"]]["url"] = "https://example.test/file.xls"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                derive_port_series(raw, arts, "synthetic")

    def test_comparative_revision_never_replaces_primary(self):
        raw, arts = cumulative_fixture()
        revision = deepcopy(raw[12])
        revision["source_id"], revision["value"] = "later", "99999"
        arts["later"] = {"source_id": "later", "publication_year": 2025, "publication_month": 1,
                         "url": "https://example.test/uploads/2025/02/revision.xls"}
        raw.append(revision)
        self.assertEqual(derive_port_series(raw, arts, "synthetic")[12]["value_tonnes"], "110")

    def test_url_month_is_not_reference_or_download_month(self):
        art = {"source_id": "x", "publication_year": 2024, "downloaded_at": "2026-11-08",
               "url": "https://example.test/uploads/2025/01/december.xls"}
        self.assertEqual(source_available_by(art), "2025-01-31")
        with self.assertRaises(ValueError):
            month_index("2025-13")


class PortModelTests(unittest.TestCase):
    def test_features_use_only_three_previous_months(self):
        a = [10., 20., 30., 1000.]
        b = [10., 20., 30., -9999.]
        self.assertEqual(features(a, 3), features(b, 3))
        self.assertEqual(features(a, 3)[:2], [30., 20.])
        with self.assertRaises(ValueError):
            features([10, None, 30, 40], 3)

    def test_ridge_solves_known_standardized_problem(self):
        history = [float(i * i + 10) for i in range(24)]
        state = fit_port_model("ridge_1", history)
        x = np.array([features(history, i) for i in range(3, 24)])
        y = np.array(history[3:])
        z = (x - x.mean(axis=0)) / x.std(axis=0)
        # Независимая проверка через расширенную задачу наименьших квадратов.
        expected = np.linalg.lstsq(np.vstack([z, np.eye(4)]),
                                   np.concatenate([y - y.mean(), np.zeros(4)]), rcond=None)[0]
        np.testing.assert_allclose(state["coefficients"], expected, rtol=1e-10, atol=1e-10)

    def test_recursive_prediction_and_unneeded_missing_seasonal_bridge(self):
        history = [100. + i for i in range(24)]
        history[11], history[23] = None, None
        seasonal = fit_port_model("seasonal_naive", history)
        self.assertEqual(forecast_values(seasonal, [24, 25, 26]), [112., 113., 114.])
        ridge = fit_port_model("ridge_1", history)
        batch = forecast_values(ridge, [24, 25, 26])
        first = forecast_values(ridge, [23])[0]
        next_state = {**ridge, "history": history[:23] + [first], "last_known_index": 23}
        self.assertEqual(batch, forecast_values(next_state, [24, 25, 26]))

    def test_bootstrap_is_reproducible_nonnegative_and_nested(self):
        state = fit_port_model("ridge_10", [20. + 10 * math.sin(i) for i in range(36)])
        first = bootstrap_predictions(state, 12, simulations=200, seed=42)
        self.assertEqual(first, bootstrap_predictions(state, 12, simulations=200, seed=42))
        for row in first:
            a, b = row["interval"]["80"], row["interval"]["95"]
            self.assertTrue(0 <= b["lower"] <= a["lower"] <= row["value"] <= a["upper"] <= b["upper"])


class PortEvaluationTests(unittest.TestCase):
    def test_publication_mask_excludes_future_december_without_hiding_known_history(self):
        rows = monthly_fixture()
        prefix = [r for r in rows if r['period'] <= '2024-12']
        history = available_history(prefix, "2024-12-31")
        self.assertIsNone(history[-1])
        self.assertIsNotNone(history[-13])
        self.assertIsNotNone(history[-2])
        self.assertEqual(available_history(prefix, "2025-01-31")[-1], float(prefix[-1]["value_tonnes"]))

    def test_holdout_changes_cannot_change_selection_or_development(self):
        first = monthly_fixture()
        second = deepcopy(first)
        for row in second[-6:]:
            row["value_tonnes"] = str(float(row["value_tonnes"]) * 50)
        a, ma, sa = evaluate_port(first)
        b, mb, sb = evaluate_port(second)
        self.assertEqual(sa, sb)
        self.assertEqual([r for r in a if r["stage"] == "development"],
                         [r for r in b if r["stage"] == "development"])
        self.assertNotEqual(ma, mb)

    def test_training_uses_only_available_past_and_three_months_do_not_update_with_truth(self):
        rows = monthly_fixture()
        recorded = []
        def fitter(name, history):
            recorded.append((name, list(history)))
            return fit_port_model(name, history)
        checked, _, _ = evaluate_port(rows, fitter=fitter)
        self.assertEqual(len(checked), 144)
        first = next(r for r in checked if r["model_id"] == "drift")
        self.assertEqual(first["train_end"], "2024-05")
        self.assertEqual(first["model_steps"], 2)
        for name, history in recorded[:4]:
            self.assertEqual(len(history), 25)
            self.assertIsNone(history[-1])
        three = [r for r in checked if r["model_id"] == "drift" and r["horizon_months"] == 3
                 and r["issued_at"] == "2024-06-30"]
        state = fit_port_model("drift", available_history(rows[:25], "2024-06-30"))
        self.assertEqual([r["predicted_tonnes"] for r in three], forecast_values(state, [25, 26, 27]))


class PortArtifactTests(unittest.TestCase):
    def test_run_repeat_reload_and_protection_of_annual_pointer(self):
        rows = monthly_fixture()
        provenance = {"data_version": "synthetic", "input_sha256": "synthetic", "sources": [],
                      "availability_method": "synthetic", "human_methodology_review": "pending"}
        with TemporaryDirectory() as folder, patch("ml.port_run.load_port_series", return_value=(rows, provenance)):
            root = Path(folder)
            write_json(root / "models/latest.json", {"sentinel": "existing_line"})
            pointer_before = (root / "models/latest.json").read_bytes()
            first = run_port(root, plots=False)
            response = forecast_port(root=root)
            self.assertEqual(len(response["predictions"]), 12)
            self.assertEqual(response["predictions"][0]["timing"], "nowcast")
            self.assertIsNone(response["direction"])
            self.assertEqual(response["training_status"], "estimated")
            before = {p.name: p.read_bytes() for p in first["output_dir"].iterdir()}
            second = run_port(root, plots=False)
            self.assertEqual(first["model_version"], second["model_version"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in first["output_dir"].iterdir()})
            self.assertEqual(pointer_before, (root / "models/latest.json").read_bytes())
            for invalid in ("2025-12", "2031-01", "2026-13", True, None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    forecast_port(invalid, root=root)
            pointer = json.loads((root / "models/port/latest.json").read_text(encoding="utf-8"))
            model = root / pointer["model_path"]
            model.write_bytes(model.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                forecast_port(root=root)

    def test_missing_and_escaping_artifacts_are_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "отсутствует"):
                forecast_port(root=root)
            write_json(root / "models/port/latest.json", {"model_path": "../outside", "sha256": "x"})
            with self.assertRaisesRegex(ValueError, "выходит"):
                forecast_port(root=root)


class AnnualExperimentTests(unittest.TestCase):
    def test_fit_failure_in_one_direction_does_not_discard_the_other(self):
        from ml.models import fit_model
        years = list(range(2011, 2024))
        series = {"inbound": {"years": years, "values": [100.] * 13},
                  "outbound": {"years": years, "values": [50.] * 13}}
        def fitter(name, values):
            if name == "ses" and max(values) > 80:
                raise ValueError("Синтетический отказ одного направления")
            return fit_model("naive", values)
        rows, _, _ = evaluate_line_experiment(series, fitter=fitter)
        self.assertTrue(all(r["fit_status"] == "ok" for r in rows if r["strategy"] == "directional"
                            and r["direction"] == "outbound" and r["model_id"] == "ses"))

    def test_aggregate_distribution_only_uses_training_years_and_selection_ignores_holdout(self):
        years = list(range(2011, 2024))
        series = {"inbound": {"years": years, "values": [100. + i for i in range(13)]},
                  "outbound": {"years": years, "values": [50. + i for i in range(13)]}}
        def fitter(name, values):
            from ml.models import fit_model
            return fit_model("naive", values)
        a, _, chosen = evaluate_line_experiment(series, fitter=fitter)
        changed = deepcopy(series)
        changed["inbound"]["values"][-2:] = [9999., 1.]
        b, _, other = evaluate_line_experiment(changed, fitter=fitter)
        self.assertEqual(chosen, other)
        self.assertEqual([r for r in a if r["stage"] == "development"], [r for r in b if r["stage"] == "development"])
        pair = [r for r in a if r["history_start"] == 2015 and r["strategy"] == "aggregate"
                and r["period"] == 2020 and r["model_id"] == "naive"]
        self.assertAlmostEqual(sum(r["predicted_tonnes"] for r in pair), 108 + 58)
        total_in = sum(series["inbound"]["values"][4:9])
        total_out = sum(series["outbound"]["values"][4:9])
        self.assertAlmostEqual(pair[0]["predicted_tonnes"], 166 * total_in / (total_in + total_out))


if __name__ == "__main__":
    unittest.main()
