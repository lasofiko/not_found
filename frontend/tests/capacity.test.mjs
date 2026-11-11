import assert from 'node:assert/strict'
import test from 'node:test'
import { calculateCapacity, requestJson } from '../src/api.ts'

const input = { period: '2027-01', demand_tonnes: 100, capacity_tonnes: 80, capacity_delta_tonnes: 5, assumption_source: 'Ввод пользователя' }
const response = { status: 'scenario', unit: 'tonnes', period: input.period, assumption_source: input.assumption_source,
  baseline_shortfall_tonnes: 20, scenario_capacity_tonnes: 85, scenario_shortfall_tonnes: 15,
  shortfall_reduction_tonnes: 5, scenario_spare_capacity_tonnes: 0, warning: 'По введённым допущениям; не прогноз.' }

test('калькулятор отправляет ввод в API с настроенным префиксом', async () => {
  const original = global.fetch
  global.fetch = async (url, options) => {
    assert.equal(url, '/custom/v2/scenarios/capacity')
    assert.equal(options.method, 'POST')
    assert.equal(options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(options.body), input)
    return Response.json(response)
  }
  try { assert.deepEqual(await calculateCapacity('/custom/v2', input), response) }
  finally { global.fetch = original }
})

test('ошибки валидации backend показывают причину, чужой период и некорректные числа отклоняются', async () => {
  const original = global.fetch
  try {
    global.fetch = async () => Response.json({ error: { message: 'Некорректные параметры запроса', details: [{ message: 'Период должен содержать месяц 01–12' }] } }, { status: 422 })
    await assert.rejects(calculateCapacity('/api/v1', input), /месяц 01–12/)
    global.fetch = async () => Response.json({ ...response, period: '2028' })
    await assert.rejects(calculateCapacity('/api/v1', input), /других параметров/)
    global.fetch = async () => Response.json({ ...response, scenario_shortfall_tonnes: null })
    await assert.rejects(calculateCapacity('/api/v1', input), /некорректный/)
    global.fetch = async () => Response.json({ ...response, status: 'forecast' })
    await assert.rejects(calculateCapacity('/api/v1', input), /некорректный/)
    global.fetch = async () => Response.json({ message: 'Модель не подключена' }, { status: 503 })
    await assert.rejects(requestJson('/api/v1/forecasts', value => value), /Модель не подключена/)
  } finally { global.fetch = original }
})
