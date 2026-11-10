"""Проверки нового протокола без использования закрытых значений 2026 года."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ml.port_evaluation import evaluate_port, load_port_protocol, PROTOCOL
from ml.port_models import MODEL_IDS, fit_port_model, forecast_values
from ml.port_run import blind_check
from pipeline.common import write_csv
from pipeline.model_inputs import month_index, month_period
from pipeline.port_protocol import derive_versions, series_asof
from test_port_ml import monthly_fixture


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
            first = blind_check(root, 'kaliningrad_ferries', state, state, rows, load_port_protocol())
            self.assertEqual(first['comparisons'][0]['predicted_tonnes'], 800)
            self.assertEqual(first['comparisons'][1]['predicted_tonnes'], 100)
            self.assertTrue(first['provided_september_difference_matches'])
            # Изменение уже просмотренного факта не запускает новое сравнение.
            cumulative[0]['value'] = '999999'
            write_csv(root / 'data/processed/port_cabotage_cumulative.csv', fields, cumulative)
            self.assertEqual(blind_check(root, 'kaliningrad_ferries', state, state, rows, load_port_protocol()), first)
            with self.assertRaisesRegex(ValueError, 'уже просмотрена'):
                blind_check(root, 'kaliningrad_ferries', fit_port_model('naive', [110.] * 43), state, rows, load_port_protocol())


if __name__ == '__main__':
    unittest.main()
