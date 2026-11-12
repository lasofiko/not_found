import assert from 'node:assert/strict'
import test from 'node:test'
import { act, browser, component, createElement as h } from './dom-harness.mjs'

const { PortForecastPanel } = await component('PortForecastPanel', [{ name: 'chart-and-select-presentation', setup(build) {
  build.onLoad({ filter: /FilterSelect\.tsx$/ }, () => ({ loader: 'jsx', contents: `
    export function FilterSelect({label, value, onChange, options}) {
      return <select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    }` }))
  build.onResolve({ filter: /^recharts$/ }, () => ({ path: 'charts', namespace: 'port-test' }))
  build.onLoad({ filter: /.*/, namespace: 'port-test' }, () => ({ loader: 'js', contents:
    ['Area', 'CartesianGrid', 'ComposedChart', 'Line', 'ResponsiveContainer', 'Tooltip', 'XAxis', 'YAxis']
      .map(name => `export function ${name}({children}) { return children ?? null }`).join('\n') }))
} }])

// Все числа синтетические; реальные модели и внешняя сеть в этих тестах не используются.
const quality = { selected_model: 'ses', model_version: 'test-model',
  selection: { period: '2024-07..2025-06', selected_WAPE: 10, naive_WAPE: 20 },
  holdout: { period: '2025-07..2025-12', selected_WAPE: 10, naive_WAPE: 20, observations: 6 },
  blind_2026: ['2026-01..2026-08', '2026-09'].map(period => ({ period, selected_APE: 10, naive_APE: 20, reference_status: 'estimated' })),
  blind_verdict: 'better_than_naive_in_both' }
const targets = [
  { target_id: 'kaliningrad_ferries', title: 'Паромы теста', display: 'primary' },
  { target_id: 'petropavlovsk_total', title: 'История теста', display: 'history_only' },
].map(row => ({ ...row, role: 'Тест', note: 'Синтетический пример', expected_model_version: 'test-model',
  port: 'Порт теста', cargo_group: 'Группа теста', model_connected: true, quality }))
const catalog = { status: 'ready', version: 'test', primary_target: targets[0].target_id, targets, warnings: [] }
function forecast(target) {
  return { ...target, status: 'ready', unit: 'tonnes', granularity: 'month', selected_model: 'ses',
    model_version: 'test-model', data_version: 'test-data', forecast_origin: '2025-12',
    result_type: target.display === 'history_only' ? 'history' : 'forecast',
    training_period: { start: '2025-12', end: '2025-12' }, training_data_available_by: '2026-01-31',
    generated_at: '2026-11-12T00:00:00Z', provenance: { source_ids: ['synthetic-source-only'] },
    history: [{ period: '2025-12', value: 100, status: 'estimated' }],
    predictions: target.display === 'history_only' ? [] : [{ period: '2026-01', value: 120, status: 'forecast',
      experimental: false, interval: { '80': { lower: 100, upper: 140 }, '95': { lower: 90, upper: 150 } } }],
    quality, warnings: ['Расчётные месячные разности, не наблюдённый OD.'] }
}
function configOrCatalog(url) {
  if (url === '/config.json') return Response.json({ apiBase: '/custom-api' })
  if (url.endsWith('/port-forecasts')) return Response.json(catalog)
}

test('панель показывает основной прогноз и происхождение, а для history_only скрывает прогноз', async t => {
  const page = browser(); t.after(() => page.close())
  global.fetch = async url => configOrCatalog(url) ?? Response.json(forecast(targets.find(row => url.includes(row.target_id))))
  await page.render(h(PortForecastPanel, { light: true }))
  assert.match(page.container.textContent, /Прогноз на 2026-01/)
  assert.match(page.container.textContent, /test-data/)
  assert.match(page.container.textContent, /synthetic-source-only/)
  assert.match(page.container.textContent, /estimated/)
  const select = page.container.querySelector('[aria-label="Порт и группа грузов"]')
  select.value = 'petropavlovsk_total'
  await act(async () => page.event(select, 'change'))
  assert.match(page.container.textContent, /Прогноз не публикуется/)
  assert.doesNotMatch(page.container.textContent, /Прогноз на 2026-01/)
})

test('смена цели отменяет запрос, запоздалый прогноз не заменяет историю', async t => {
  const page = browser(); t.after(() => page.close()); let pending
  global.fetch = async (url, options) => configOrCatalog(url) ?? (url.includes('kaliningrad_ferries')
    ? new Promise(resolve => { pending = { resolve, signal: options.signal } })
    : Response.json(forecast(targets[1])))
  await page.render(h(PortForecastPanel, { light: false }))
  const select = page.container.querySelector('[aria-label="Порт и группа грузов"]')
  select.value = 'petropavlovsk_total'
  await act(async () => page.event(select, 'change'))
  assert.equal(pending.signal.aborted, true)
  await act(async () => pending.resolve(Response.json(forecast(targets[0]))))
  assert.match(page.container.textContent, /Прогноз не публикуется/)
  assert.doesNotMatch(page.container.textContent, /Прогноз на 2026-01/)
})
