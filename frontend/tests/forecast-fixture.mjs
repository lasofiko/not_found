// Только вымышленные числа для проверки схемы/экспорта. Не модель приложения.
export const syntheticForecast = (direction = 'inbound', end = 2030) => ({
  status: 'ready', result_type: 'forecast', target_id: 'ust_luga_kaliningrad_ferry',
  target_name: 'Синтетическая проверка узкой морской линии', region: 'kaliningrad', direction, mode: 'sea', unit: 'tonnes',
  geography: { origin: direction === 'inbound' ? 'rest_of_russia' : 'kaliningrad_oblast', destination: direction === 'inbound' ? 'kaliningrad_oblast' : 'rest_of_russia', scope: 'Синтетический узкий охват; не весь коридор', route_id: 'synthetic_ust_luga_baltiysk' },
  training_period: { start: '2015', end: '2023' }, forecast_origin: 2023, observations: 9, selected_model: 'synthetic_test',
  data_version: 'synthetic-data-v1', model_version: 'synthetic-model-v1', generated_at: '2026-11-12T00:00:00Z',
  predictions: Array.from({ length: end - 2023 }, (_, index) => ({ period: String(2024 + index), period_granularity: 'year', horizon_years: index + 1, value: 100 + index,
    interval: { '80': { lower: 90, upper: 120 }, '95': { lower: 80, upper: 130 } } })),
  provenance: { source_ids: ['synthetic-test'], sources: [{ source_id: 'synthetic-test', organization: 'Вымышленная организация', url: 'https://example.invalid/synthetic' }], restrictions: 'Только тест; исходные наблюдения не распространяются' },
  metrics: { selected: { development: { MAE: 1, WAPE: 1, observations: 3 }, holdout: { MAE: 2, WAPE: 2, observations: 2 } }, baseline: { development: { MAE: 2, WAPE: 2, observations: 3 }, holdout: { MAE: 3, WAPE: 3, observations: 2 } }, selection_period: 'synthetic', holdout_period: 'synthetic', evaluation_horizon_years: 1, methodological_status: 'synthetic_test_only' },
  warnings: ['Синтетика; не исследовательский результат', 'Не доказан причинный эффект; "оценка"\nтребует проверки'],
})
