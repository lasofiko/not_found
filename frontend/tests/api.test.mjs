import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { calculateCapacity, capacitySchema, forecastSchema, requestJson } from '../src/api.ts'
import { createForecastReport } from '../src/report.ts'

test('неверный JSON и null не становятся успешным прогнозом', async () => {
  const original = global.fetch
  try {
    global.fetch = async () => new Response('{broken', { status: 200 })
    await assert.rejects(requestJson('/forecasts', value => forecastSchema.parse(value)), /некорректный/)
    global.fetch = async () => Response.json({ predictions: [{ value: null }] })
    await assert.rejects(requestJson('/forecasts', value => forecastSchema.parse(value)), /некорректный/)
    global.fetch = async () => new Response('{}', { status: 500 })
    await assert.rejects(requestJson('/forecasts', value => value), /500/)
    global.fetch = async () => { throw new TypeError('offline') }
    await assert.rejects(requestJson('/forecasts', value => value), /недоступен/)
    global.fetch = async () => new Response('', { status: 503 })
    await assert.rejects(requestJson('/config.json', value => value), /Backend недоступен/)
  } finally { global.fetch = original }
})

test('калькулятор React использует API и проверяет период ответа', async () => {
  const oldFetch = global.fetch
  const oldWindow = global.window
  global.window = { location: { origin: 'http://127.0.0.1:5173' } }
  const input = { demand_tonnes: 100, capacity_tonnes: 80, capacity_delta_tonnes: 30, period: '2027', assumption_source: 'Ввод пользователя' }
  const result = { status: 'scenario', unit: 'tonnes', period: '2027', assumption_source: input.assumption_source,
    baseline_shortfall_tonnes: 20, scenario_capacity_tonnes: 110, scenario_shortfall_tonnes: 0,
    shortfall_reduction_tonnes: 20, scenario_spare_capacity_tonnes: 10, warning: 'Сценарий, не прогноз.' }
  let wrongPeriod = false
  try {
    global.fetch = async (url, options) => {
      if (url === '/config.json') return Response.json({ apiBase: '/custom/v2' })
      assert.equal(url, 'http://127.0.0.1:5173/custom/v2/scenarios/capacity')
      assert.equal(options.method, 'POST')
      assert.deepEqual(JSON.parse(options.body), input)
      return Response.json({ ...result, period: wrongPeriod ? '2028' : result.period })
    }
    assert.deepEqual(await calculateCapacity(input), result)
    wrongPeriod = true
    await assert.rejects(calculateCapacity(input), /других входных данных/)
    assert.equal(capacitySchema.safeParse({ ...result, scenario_spare_capacity_tonnes: null }).success, false)
    assert.equal(capacitySchema.safeParse({ ...result, baseline_shortfall_tonnes: Infinity }).success, false)
  } finally { global.fetch = oldFetch; global.window = oldWindow }
})

test('таймаут и внешняя отмена завершают запрос', async () => {
  const original = global.fetch
  global.fetch = async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('abort', 'AbortError')), { once: true })
  })
  try {
    await assert.rejects(requestJson('/forecasts', value => value, undefined, 15), /ожидания/)
    const controller = new AbortController()
    const pending = requestJson('/forecasts', value => value, controller.signal)
    controller.abort()
    await assert.rejects(pending, { name: 'AbortError' })
  } finally { global.fetch = original }
})

test('реальный ответ API проходит схему, все годы CSV совпадают с API', { skip: !process.env.NF_FORECAST_FIXTURE }, () => {
  const result = forecastSchema.parse(JSON.parse(readFileSync(process.env.NF_FORECAST_FIXTURE, 'utf8')))
  const csv = createForecastReport(result)
  assert.equal(csv.charCodeAt(0), 0xFEFF)
  const rows = csv.slice(1).trim().split('\r\n').slice(1).map(row => row.split(';').map(cell => cell.slice(1, -1)))
  assert.equal(rows.length, result.predictions.length)
  rows.forEach((row, index) => {
    assert.equal(row[0], result.predictions[index].period)
    assert.equal(Number(row[1]), result.predictions[index].value)
    assert.equal(row[2], 'tonnes')
    assert.equal(row[8], 'forecast')
    assert.equal(row[10], result.model_version)
  })
  const invalid = structuredClone(result)
  invalid.predictions[0].value = null
  assert.equal(forecastSchema.safeParse(invalid).success, false)
})
