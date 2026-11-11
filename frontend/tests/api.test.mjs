import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { forecastSchema, requestJson } from '../src/api.ts'
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
  } finally { global.fetch = original }
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
