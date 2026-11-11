"""Проверки расчёта гипотез на синтетических данных."""

import csv
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ml.hypotheses import (
    FLEET_PATH,
    build_panel,
    fit_ols,
    load_fleet,
    run_hypotheses,
    turnaround_gain,
    turnaround_grid,
    weather_by_month,
)
from pipeline.common import digest, write_json


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _freight(
    period: str,
    value: float,
    scope: str = "оба направления; паромы Усть-Луга — Балтийск",
    precision: str = "as_published",
) -> dict:
    return {
        "series_id": "ferry_monthly",
        "direction_scope": "both",
        "period": period,
        "value": value,
        "measurement_scope": scope,
        "precision": precision,
    }


def _weather(period: str, wind: float = 20.0) -> dict:
    return {
        period: {"wind_max_mean_kmh": wind, "precipitation_mm": 1.0, "strong_wind_days_40": 0, "strong_wind_days_50": 0}
    }


class TurnaroundTests(unittest.TestCase):
    def test_gain_matches_cycle_formula(self):
        self.assertAlmostEqual(turnaround_gain(45, 36, 0.2), 126 / (126 - 7.2) - 1)

    def test_no_cut_means_no_gain(self):
        self.assertEqual(turnaround_gain(45, 36, 0.0), 0.0)

    def test_grid_range_is_positive(self):
        gains = [row[3] for row in turnaround_grid()]
        self.assertEqual(len(gains), 18)
        self.assertLess(min(gains), max(gains))
        self.assertGreater(min(gains), 0)


class FleetTests(unittest.TestCase):
    def test_shipped_fleet_table_is_consistent(self):
        fleet = load_fleet()
        self.assertTrue(all(1 <= row["active_ferries"] <= 6 and len(row["quote"]) <= 80 for row in fleet.values()))
        self.assertTrue(all(row["review_status"] == "pending" for row in fleet.values()))

    def test_duplicate_month_is_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fleet.csv"
            row = {
                "period": "2024-01",
                "active_ferries": 3,
                "source_url": "u",
                "quote": "q",
                "extraction": "x",
                "review_status": "pending",
            }
            _write(path, [row, row])
            with self.assertRaisesRegex(ValueError, "Повтор"):
                load_fleet(path)


class PanelTests(unittest.TestCase):
    def test_weather_uses_one_point_and_counts_strong_wind_days(self):
        rows = [
            {"geography": "Балтийск", "metric": "wind_speed_10m_max", "period": f"2024-01-0{d}", "value": v}
            for d, v in ((1, 39.9), (2, 40.0), (3, 55.0))
        ]
        rows.append({"geography": "Калининград", "metric": "wind_speed_10m_max", "period": "2024-01-01", "value": 99})
        result = weather_by_month(rows)["2024-01"]
        self.assertAlmostEqual(result["wind_max_mean_kmh"], (39.9 + 40 + 55) / 3)
        self.assertEqual((result["strong_wind_days_40"], result["strong_wind_days_50"]), (2, 1))

    def test_panel_marks_scope_and_lower_bound_and_skips_other_series(self):
        fleet = {
            "2024-01": {"active_ferries": 3, "quote": "q", "review_status": "pending", "source_url": "u"},
            "2024-02": {"active_ferries": 5, "quote": "q", "review_status": "pending", "source_url": "u"},
        }
        freight = [
            _freight("2024-01", 90.0, precision="lower_bound"),
            _freight("2024-02", 100.0, "оба направления; паромы, включая Антей"),
            {**_freight("2024-01", 1.0), "series_id": "ferry_forms"},
        ]
        panel = build_panel(freight, fleet, {**_weather("2024-01"), **_weather("2024-02")})
        self.assertEqual(len(panel), 2)
        self.assertTrue(panel[0]["lower_bound"] and panel[0]["in_main_sample"])
        self.assertAlmostEqual(panel[0]["tonnes_per_ferry"], 30.0)
        self.assertTrue(panel[1]["wider_scope"])
        self.assertFalse(panel[1]["in_main_sample"])

    def test_missing_weather_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Нет погоды"):
            build_panel(
                [_freight("2024-01", 90.0)],
                {"2024-01": {"active_ferries": 3, "quote": "q", "review_status": "p", "source_url": "u"}},
                {},
            )

    def test_regression_recovers_known_ferry_effect(self):
        panel = [
            {
                "active_ferries": 3 + i % 2,
                "wind_max_mean_kmh": 20 + (i * 7) % 11,
                "tonnes": 10_000 + 30_000 * (3 + i % 2) - 100 * (20 + (i * 7) % 11) + (-1) ** i * 50,
            }
            for i in range(24)
        ]
        result = fit_ols(panel, True)
        self.assertAlmostEqual(result["ferries_coef_tonnes"], 30_000, delta=200)
        self.assertAlmostEqual(result["wind_coef_tonnes_per_kmh"], -100, delta=30)


class RunTests(unittest.TestCase):
    def _research(self, root, version, error):
        directory = root / "reports/generated/ml-line-research" / version
        metrics = {strategy: {direction: {"holdout": {"MAE": error if strategy == "directional" else 20}}
                             for direction in ("inbound", "outbound")}
                   for strategy in ("directional", "aggregate")}
        write_json(directory / "metrics.json", {"data_version": "test", "metrics": {"2015": metrics}})
        write_json(directory / "run_manifest.json", {"report_version": version, "data_version": "test", "outputs": {
            "metrics.json": digest((directory / "metrics.json").read_bytes())}})
        write_json(directory.parent / "latest.json", {"report_version": version,
            "report_path": (directory / "report.html").relative_to(root).as_posix()})
        return directory

    def test_selected_research_changes_version_and_preserves_previous_result(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._bundle(root)
            without = run_hypotheses(root)
            self.assertIsNone(json.loads((without["output_dir"] / "run_manifest.json").read_text())["line_research"])
            first_source = self._research(root, "research-a", 10)
            first = run_hypotheses(root)
            old_bytes = (first["output_dir"] / "hypothesis_results.csv").read_bytes()
            self.assertNotEqual(first["version"], without["version"])
            second_source = self._research(root, "research-b", 15)
            os.utime(first_source / "metrics.json", (2_000_000_000, 2_000_000_000))
            second = run_hypotheses(root)
            self.assertNotEqual(first["version"], second["version"])
            self.assertNotEqual(old_bytes, (second["output_dir"] / "hypothesis_results.csv").read_bytes())
            self.assertEqual(old_bytes, (first["output_dir"] / "hypothesis_results.csv").read_bytes())
            manifest = json.loads((second["output_dir"] / "run_manifest.json").read_text())
            self.assertEqual(manifest["line_research"]["report_version"], "research-b")
            self.assertEqual(manifest["line_research"]["metrics_sha256"], digest((second_source / "metrics.json").read_bytes()))
            self.assertEqual(manifest["fingerprint"]["line_research"], manifest["line_research"])
            os.utime(second_source / "metrics.json", (1_000_000_000, 1_000_000_000))
            self.assertEqual(run_hypotheses(root)["version"], second["version"])
            self._research(root, "research-b", 17)
            self.assertNotEqual(run_hypotheses(root)["version"], second["version"])

    def test_corrupt_or_escaping_research_pointer_is_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._bundle(root)
            directory = self._research(root, "research-a", 10)
            (directory / "metrics.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                run_hypotheses(root)
            self.assertFalse((root / "reports/generated/ml-hypotheses").exists())
            write_json(directory.parent / "latest.json", {"report_version": "research-a", "report_path": "../outside/report.html"})
            with self.assertRaisesRegex(ValueError, "выходит"):
                run_hypotheses(root)

    def _bundle(self, root: Path) -> None:
        processed = root / "data/processed"
        fleet = load_fleet()
        freight = [
            {
                "series_id": "ferry_monthly",
                "direction_scope": "both",
                "period": p,
                "value": 28_000 * v["active_ferries"] + 500 * (i % 5),
                "measurement_scope": "оба направления; паромы Усть-Луга — Балтийск",
                "precision": "as_published",
            }
            for i, (p, v) in enumerate(fleet.items())
        ]
        weather = [
            {"geography": "Балтийск", "metric": "wind_speed_10m_max", "period": f"{p}-15", "value": 20 + i % 7}
            for i, p in enumerate(fleet)
        ]
        weather += [
            {"geography": "Балтийск", "metric": "precipitation_sum", "period": f"{p}-15", "value": 1} for p in fleet
        ]
        candidate = [
            {"period": y, "direction": d, "value_tonnes": v}
            for y in (2022, 2023)
            for d, v in (("inbound", 100), ("outbound", 40))
        ]
        terminal = [
            {
                "period": "2025-05",
                "operator": "Росморпорт",
                "cargo_group": "Всего грузов",
                "measurement_scope": "operator",
                "value": 200_000,
            }
        ]
        _write(processed / "supplemental_freight.csv", freight)
        _write(processed / "weather_daily.csv", weather)
        _write(processed / "candidate_observations.csv", candidate)
        _write(processed / "port_cabotage_monthly.csv", terminal)
        (processed / "build_manifest.json").write_text(json.dumps({"data_version": "test"}), encoding="utf-8")

    def test_run_writes_outputs_and_labels_results_without_observed_status(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._bundle(root)
            result = run_hypotheses(root)
            for name in (
                "ferry_month_panel.csv",
                "fleet_regression.csv",
                "hypothesis_results.csv",
                "hypotheses-report.md",
                "run_manifest.json",
            ):
                self.assertTrue((result["output_dir"] / name).is_file(), name)
            with (result["output_dir"] / "hypothesis_results.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(
                {r["status"] for r in rows} <= {"косвенная оценка", "сценарный результат действия", "не рассчитано"}
            )
            self.assertIn("не рассчитано", {r["status"] for r in rows if r["hypothesis_id"] == "separate_forecast"})
            self.assertEqual(run_hypotheses(root)["version"], result["version"])

    def test_missing_bundle_gives_clear_error(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(FileNotFoundError, "build_manifest"):
                run_hypotheses(Path(folder))

    def test_saved_annual_forecast_enables_year_specific_action_table(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._bundle(root)
            (root / "models").mkdir()
            (root / "models/latest.json").write_text("{}")
            def saved_forecast(direction, end_year, root):
                return {"predictions": [
                    {"period": str(year), "value": 100.0, "interval": {"80": {"upper": 150.0}}}
                    for year in (2027, 2028)]}
            with patch("ml.hypotheses.forecast", side_effect=saved_forecast):
                result = run_hypotheses(root)
            with (result["output_dir"] / "action_scenarios.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            reserve = {r["year"]: r for r in rows if r["direction"] == "inbound"
                       and r["demand_case"] == "demand_x1_5" and r["action"] == "reserve_ferry"}
            self.assertNotEqual(reserve["2027"]["reserve_gain_share"], "")
            self.assertEqual(reserve["2028"]["capacity_action_tonnes"], "")
            self.assertEqual(reserve["2027"]["capacity_rail_limited_tonnes"], "")
            report = (result["output_dir"] / "hypotheses-report.md").read_text()
            self.assertIn("Прирост от резерва", report)

    def test_shipped_fleet_path_exists(self):
        self.assertTrue(FLEET_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
