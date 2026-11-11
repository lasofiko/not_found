"""Проверки нового протокола без использования закрытых значений 2026 года."""
import json
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_port_ml import monthly_fixture
from test_supplemental import port_fixture

from ml.port_evaluation import PROTOCOL, evaluate_port, load_port_protocol
from ml.port_models import MODEL_IDS, fit_port_model, forecast_values
from ml.port_run import blind_check
from pipeline.common import write_csv
from pipeline.model_inputs import month_index, month_period
from pipeline.port_protocol import derive_versions, load_blind_cumulatives, series_asof

BINDING = {"model_version": "synthetic-model", "data_version": "synthetic", "model_sha256": "a" * 64}


def cumulative_fixture():
    rows, artifacts = [], {}
    for year in (2022, 2023, 2024, 2025):
        for month in range(1, 13):
            period = f'{year}-{month:02d}'
            sid = f'primary_{period}'
            upload = month_period(month_index(period) + 1)
            artifacts[sid] = {'source_id': sid, 'publication_year': year, 'publication_month': month,
                'url': 'https://example.test/uploads/' + upload.replace('-', '/') + '/file.xls'}
            rows.append({'period': period, 'port': 'Порт Калининград', 'cargo_group': 'грузы на паромах',
                'measurement_scope': 'port_total', 'unit': 'tonnes', 'period_granularity': 'year_to_date',
                'value': str(month * 100), 'status': 'observed', 'data_version': 'synthetic',
                'source_id': sid, 'source_locator': 'sheet!D10'})
    return rows, artifacts


class ProtocolTests(unittest.TestCase):
    def test_far_east_blind_reads_only_two_raw_issues_and_validates_columns(self):
        artifacts = [{"source_id": f"synthetic_{month}", "dataset_kind": "port_cabotage",
                      "publication_year": 2026, "publication_month": month,
                      "sha256": "a" * 64, "local_path": f"data/raw/synthetic/{month}.xls"} for month in (8, 9)]
        def book(_, artifact):
            month = artifact["publication_month"]
            fixture = port_fixture(month)
            cells = fixture[0][1]
            cells[1]["A"] = f"Объём перевалки за {month} месяцев 2026 г."
            cells[4]["A"] = "Порт Владивосток"
            cells[8]["A"] = "Итого Порт Владивосток"
            for row in cells.values():
                if row.get("B") == "25":
                    row["B"] = "26"
                elif row.get("B") == "24":
                    row["B"] = "25"
            return fixture
        with patch("pipeline.port_protocol.load_registry", return_value=artifacts), patch(
                "pipeline.bundle_sources.sheets", side_effect=book) as reader:
            rows, metadata = load_blind_cumulatives(Path("."), "vladivostok_total")
        self.assertEqual({r["period"] for r in rows}, {"2026-08", "2026-09"})
        self.assertTrue(all(r["measurement_scope"] == "port_total" for r in rows))
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(metadata["raw_source_validation"], "performed_for_target_cells")

    def test_far_east_blind_lock_precedes_read_and_success_is_cached(self):
        state = fit_port_model("naive", [100.] * 43)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fields = ["period", "port", "cargo_group", "measurement_scope", "value", "unit", "status",
                      "source_id", "source_locator"]
            write_csv(root / "data/processed/port_cabotage_monthly.csv", fields, [])
            destination = root / "reports/generated/ml-port/blind_checks/vladivostok_total.json"
            def closed_values(*_):
                self.assertEqual(json.loads(destination.read_text())["status"], "started")
                base = {"port": "Порт Владивосток", "cargo_group": "Всего грузов", "measurement_scope": "port_total",
                        "unit": "tonnes", "status": "observed", "source_locator": "synthetic!C9"}
                return [{**base, "period": "2026-08", "value": "800", "source_id": "aug"},
                        {**base, "period": "2026-09", "value": "900", "source_id": "sep"}], {
                    "raw_source_validation": "performed_for_target_cells", "source_artifacts": []}
            with patch("pipeline.port_protocol.load_blind_cumulatives", side_effect=closed_values) as loader:
                first = blind_check(root, "vladivostok_total", state, state, monthly_fixture(), load_port_protocol(), **BINDING)
                second = blind_check(root, "vladivostok_total", state, state, monthly_fixture(), load_port_protocol(), **BINDING)
                self.assertEqual(first, second)
                self.assertEqual(loader.call_count, 1)
                self.assertEqual({key: first[key] for key in BINDING}, BINDING)
                with self.assertRaisesRegex(ValueError, "другой версией"):
                    blind_check(root, "vladivostok_total", state, state, monthly_fixture(), load_port_protocol(),
                                **{**BINDING, "model_version": "new-model"})
                self.assertEqual(loader.call_count, 1)
            self.assertIsNone(first["provided_september_difference_matches"])
            destination.write_text(json.dumps({"selection_lock": first["selection_lock"], "status": "started"}))
            with self.assertRaisesRegex(ValueError, "не завершена"):
                blind_check(root, "vladivostok_total", state, state, monthly_fixture(), load_port_protocol(), **BINDING)

    def test_rules_are_frozen_and_ridge_is_not_a_candidate(self):
        self.assertEqual(load_port_protocol(), PROTOCOL)
        self.assertEqual(MODEL_IDS, ('naive', 'seasonal_naive', 'drift', 'ses'))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'changed.toml'
            original = Path('ml/port_protocol.toml').read_text()
            path.write_text(original.replace('2024-07', '2025-01'))
            with self.assertRaisesRegex(ValueError, 'протокол'):
                load_port_protocol(path)

    def test_43_months_reset_in_january_and_ignore_late_revision(self):
        raw, arts = cumulative_fixture()
        original = deepcopy(raw)
        later = {**raw[-1], 'source_id': 'revision', 'value': '999999'}
        arts['revision'] = {'source_id': 'revision', 'publication_year': 2026, 'publication_month': 12,
                           'url': 'https://example.test/uploads/2027/01/file.xls'}
        raw.append(later)
        versions, _ = derive_versions(raw, arts, 'synthetic', 'kaliningrad_ferries')
        result = series_asof(versions, 'kaliningrad_ferries')
        self.assertEqual(len(result), 43)
        self.assertTrue(all(Decimal(r['value_tonnes']) == 100 for r in result))
        self.assertEqual(result[7]['period'], '2023-01')
        self.assertEqual(result[7]['previous_source_id'], '')
        self.assertEqual(raw[:-1], original)

    def test_negative_difference_is_missing_not_zero_and_calendar_is_kept(self):
        raw, arts = cumulative_fixture()
        next(r for r in raw if r['period'] == '2024-06')['value'] = '0'
        versions, issues = derive_versions(raw, arts, 'synthetic', 'kaliningrad_ferries')
        result = series_asof(versions, 'kaliningrad_ferries')
        june = next(r for r in result if r['period'] == '2024-06')
        self.assertEqual(june['value_tonnes'], '')
        self.assertEqual(len(result), 43)
        self.assertTrue(any(r['quality_flags'] == 'negative_difference' for r in issues))

    def test_short_history_only_runs_naive_and_records_no_selection(self):
        rows = monthly_fixture()[7:]
        folds, metrics, chosen = evaluate_port(rows)
        self.assertEqual(chosen, 'naive')
        self.assertEqual(set(r['model_id'] for r in folds), {'naive'})
        self.assertEqual(metrics['naive']['selection_status'], 'not_performed_insufficient_history')

    def test_fit_failure_excludes_candidate_and_zero_wape_is_null(self):
        rows = monthly_fixture()
        def fitter(name, history):
            if name == 'drift' and len(history) == 25:
                raise ValueError('synthetic single fold failure')
            return fit_port_model(name, history)
        _, metrics, chosen = evaluate_port(rows, fitter=fitter)
        self.assertNotEqual(chosen, 'drift')
        self.assertGreater(metrics['drift']['development']['1']['failed_folds'], 0)
        for row in rows:
            row['value_tonnes'] = '0'
        _, metrics, _ = evaluate_port(rows)
        self.assertIsNone(metrics['naive']['development']['1']['WAPE'])
        self.assertEqual(metrics['naive']['development']['1']['MAE'], 0)

    def test_monthly_drift_is_hand_calculated_and_ses_is_recursive(self):
        state = fit_port_model('drift', [10., 20., 30.])
        self.assertEqual(forecast_values(state, [3, 4]), [40., 50.])
        state = fit_port_model('ses', [10., 20., 30., 40.])
        self.assertEqual(forecast_values(state, [4, 5]), [state['level'], state['level']])

    def test_blind_comparison_is_one_time_and_never_refits_with_2026_actuals(self):
        rows = monthly_fixture()
        state = fit_port_model('naive', [100.] * 43)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields = ['period', 'port', 'cargo_group', 'measurement_scope', 'unit', 'status', 'value',
                      'source_id', 'source_locator']
            base = {'port': 'Порт Калининград', 'cargo_group': 'грузы на паромах', 'measurement_scope': 'port_total',
                    'unit': 'tonnes', 'status': 'observed', 'source_locator': 'sheet!D10'}
            cumulative = [{**base, 'period': '2026-08', 'value': '1000', 'source_id': 'aug'},
                          {**base, 'period': '2026-09', 'value': '1200', 'source_id': 'sep'}]
            write_csv(root / 'data/processed/port_cabotage_cumulative.csv', fields, cumulative)
            write_csv(root / 'data/processed/port_cabotage_monthly.csv', fields + ['previous_source_id'],
                      [{**cumulative[1], 'value': '200', 'previous_source_id': 'aug'}])
            first = blind_check(root, 'kaliningrad_ferries', state, state, rows, load_port_protocol(), **BINDING)
            self.assertEqual(first['comparisons'][0]['predicted_tonnes'], 800)
            self.assertEqual(first['comparisons'][1]['predicted_tonnes'], 100)
            self.assertTrue(first['provided_september_difference_matches'])
            # Изменение уже просмотренного факта не запускает новое сравнение.
            cumulative[0]['value'] = '999999'
            write_csv(root / 'data/processed/port_cabotage_cumulative.csv', fields, cumulative)
            self.assertEqual(blind_check(root, 'kaliningrad_ferries', state, state, rows, load_port_protocol(), **BINDING), first)
            with self.assertRaisesRegex(ValueError, 'уже просмотрена'):
                blind_check(root, 'kaliningrad_ferries', fit_port_model('naive', [110.] * 43), state, rows, load_port_protocol(), **BINDING)


if __name__ == '__main__':
    unittest.main()
