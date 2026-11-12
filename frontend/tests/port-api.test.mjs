import assert from 'node:assert/strict'
import test from 'node:test'
import { chartRows, getPortCatalog, getPortForecast, portForecastSchema, verdictText } from '../src/port-api.ts'

const band = (v) => ({ '80': { lower: v - 10, upper: v + 10 }, '95': { lower: v - 20, upper: v + 20 } })
const quality = {
  selected_model: 'ses', model_version: 'bb-port-5b1f25b217f5',
  selection: { period: '2024-07..2025-06', selected_WAPE: 10.4, naive_WAPE: 11.4 },
  holdout: { period: '2025-07..2025-12', selected_WAPE: 12.9, naive_WAPE: 19.8, observations: 6 },
  blind_2026: [{ period: '2026-01..2026-08', selected_APE: 3.7, naive_APE: 3.8, reference_status: 'observed' },
    { period: '2026-09', selected_APE: 7.7, naive_APE: 7.9, reference_status: 'estimated' }],
  blind_verdict: 'better_than_naive_in_both',
}
const forecast = {
  status: 'ready', target_id: 'kaliningrad_ferries', title: 'Порт Калининград · грузы на паромах', role: 'Основная',
  display: 'primary', note: 'синтетический тест', port: 'Порт Калининград', cargo_group: 'грузы на паромах',
  unit: 'tonnes', granularity: 'month', selected_model: 'ses', model_version: 'bb-port-5b1f25b217f5', data_version: 'test',
  forecast_origin: '2025-12', history: [{ period: '2025-11', value: 100, status: 'estimated' }, { period: '2025-12', value: 110, status: 'estimated' }],
  result_type: 'forecast', training_period: { start: '2025-11', end: '2025-12' },
  training_data_available_by: '2026-01-31', generated_at: '2026-11-12T00:00:00Z',
  provenance: { source_ids: ['synthetic-test-only'] },
  predictions: [{ period: '2026-01', value: 120, status: 'forecast', experimental: false, interval: band(120) }], quality, warnings: [],
}

test('график соединяет историю и прогноз без разрыва', () => {
  const rows = chartRows(portForecastSchema.parse(forecast))
  assert.deepEqual(rows.map(r => r.period), ['2025-11', '2025-12', '2026-01'])
  assert.equal(rows[1].forecast, 110)
  assert.deepEqual(rows[2].band, [110, 130])
})

test('цель «только история» не может нести прогноз', () => {
  assert.throws(() => portForecastSchema.parse({ ...forecast, display: 'history_only' }))
  assert.equal(portForecastSchema.parse({ ...forecast, display: 'history_only', result_type: 'history', predictions: [] }).predictions.length, 0)
})

test('API клиента: каталог и прогноз другой цели отклоняются', async () => {
  const original = global.fetch
  try {
    global.fetch = async (url) => String(url).endsWith('/config.json') ? Response.json({ apiBase: '/api/v1' })
      : String(url).includes('/port-forecasts/') ? Response.json({ ...forecast, target_id: 'kaliningrad_total' })
        : Response.json({ status: 'ready', version: 'v', primary_target: 'kaliningrad_ferries', warnings: [],
          targets: [{ target_id: 'kaliningrad_ferries', role: 'Основная', title: 't', display: 'primary', note: 'n',
            expected_model_version: 'bb-port-5b1f25b217f5', port: 'p', cargo_group: 'c', model_connected: false, quality }] })
    globalThis.window = { location: { origin: 'http://localhost' } }
    assert.equal((await getPortCatalog()).primary_target, 'kaliningrad_ferries')
    await assert.rejects(getPortForecast('kaliningrad_ferries', '2026-12'), /другой цели/)
  } finally { global.fetch = original }
})

test('вердикт слепой проверки формулируется честно', () => {
  assert.match(verdictText('worse_than_naive_in_both'), /больше naive/)
})
