import assert from 'node:assert/strict'
import test from 'node:test'
import { forecastSchema } from '../src/api.ts'
import { createForecastReport } from '../src/report.ts'
import { syntheticForecast } from './forecast-fixture.mjs'

function csvRows(csv) {
  const rows = []; let row = []; let value = ''; let quoted = false
  for (let index = 1; index < csv.length; index++) {
    const char = csv[index]
    if (char === '"') {
      if (quoted && csv[index + 1] === '"') { value += '"'; index++ } else quoted = !quoted
    } else if (char === ';' && !quoted) { row.push(value); value = '' }
    else if (char === '\r' && !quoted && csv[index + 1] === '\n') { row.push(value); rows.push(row); row = []; value = ''; index++ }
    else value += char
  }
  return rows
}

test('самодостаточный CSV хранит числа, охват, версии и ограничения одного ответа API', () => {
  for (const direction of ['inbound', 'outbound']) for (const end of [2024, 2030]) {
    const result = forecastSchema.parse(syntheticForecast(direction, end))
    const before = structuredClone(result)
    const csv = createForecastReport(result)
    assert.equal(csv.charCodeAt(0), 0xFEFF)
    const [columns, ...rows] = csvRows(csv)
    assert.equal(rows.length, result.predictions.length)
    assert.equal(new Set(columns).size, columns.length)
    rows.forEach((values, index) => {
      assert.equal(values.length, columns.length)
      const row = Object.fromEntries(columns.map((name, offset) => [name, values[offset]]))
      assert.equal(row.period, result.predictions[index].period)
      assert.equal(Number(row.value), result.predictions[index].value)
      for (const name of ['unit', 'region', 'direction', 'mode', 'target_id', 'target_name', 'forecast_origin', 'generated_at', 'data_version', 'model_version', 'selected_model', 'observations']) assert.equal(row[name], String(result[name]))
      for (const name of ['origin', 'destination', 'scope', 'route_id']) assert.equal(row[name], result.geography[name])
      assert.equal(row.training_start, result.training_period.start)
      assert.equal(row.training_end, result.training_period.end)
      assert.equal(row.period_granularity, 'year')
      assert.equal(Number(row.horizon_years), index + 1)
      assert.equal(row.status, 'forecast')
      assert.deepEqual(JSON.parse(row.sources_json), result.provenance.sources)
      assert.deepEqual(JSON.parse(row.warnings_json), result.warnings)
      assert.deepEqual(JSON.parse(row.provenance_json), result.provenance)
      assert.deepEqual(JSON.parse(row.metrics_json), result.metrics)
      assert.equal(row.observations_json, undefined, 'исходная история не включается в выгрузку')
    })
    assert.deepEqual(result, before)
    assert.doesNotMatch(csv, /actuals|observed_values/)
  }
})
