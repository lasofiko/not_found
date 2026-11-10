import assert from 'node:assert/strict'
import test from 'node:test'
import { DIRECTIONS, SCENARIOS, selectDemoScenario } from '../src/dashboard-data.ts'
import { createDemoReport } from '../src/report.ts'

test('каждый пример согласован между карточками и тремя графиками', () => {
  for (const direction of DIRECTIONS) {
    for (const scenario of SCENARIOS) {
      const result = selectDemoScenario({ direction, scenario: scenario.id })
      const label = scenario.id === 'base' ? 'Базовый' : scenario.id === 'capacity' ? 'А. Мощность' : 'Б. Новый маршрут'
      assert.equal(result.status, 'synthetic')
      assert.match(result.source, /синтетический/)
      assert.equal(result.volume, result.stacked.at(-1)['Море'])
      assert.equal(result.volume, result.dynamics.at(-1)[label])
      assert.equal(result.volume, result.hypotheses.find((item) => item.name === label).volume)
      assert.equal(result.time, result.hypotheses.find((item) => item.name === label).time)
      assert.deepEqual(result.dynamics.map(({ year }) => year), ['2025', '2026', '2027', '2028', '2029', '2030'])
      assert.equal(result.volumeDelta, Math.round((result.volume / result.stacked[0]['Море'] - 1) * 100))
      assert.ok(result.terminal >= 0 && result.terminal <= 100)
      assert.equal(result.timeUnit, 'ч')
    }
  }
})

test('направления коридора разделены, неизвестные значения отклоняются', () => {
  assert.deepEqual(DIRECTIONS, ['inbound', 'outbound'])
  assert.notEqual(selectDemoScenario({ direction: 'inbound', scenario: 'base' }).volume,
    selectDemoScenario({ direction: 'outbound', scenario: 'base' }).volume)
  assert.throws(() => selectDemoScenario({ direction: 'Советск', scenario: 'base' }))
  assert.throws(() => selectDemoScenario({ direction: 'inbound', scenario: 'unknown' }))
})

test('CSV содержит BOM, настоящие строки, происхождение и выбранные значения', () => {
  for (const direction of DIRECTIONS) {
    for (const scenario of SCENARIOS) {
      const config = { direction, scenario: scenario.id }
      const report = createDemoReport(config)
      assert.equal(report.charCodeAt(0), 0xFEFF)
      assert.ok(report.split('\r\n').length > 10)
      assert.ok(!report.includes('\\uFEFF'))
      assert.ok(!report.includes('\\n'))
      assert.match(report, /"Статус";"synthetic"/)
      assert.match(report, /"Период";"2030"/)
      assert.ok(report.includes(`"Направление";"${direction}"`))
      assert.ok(report.includes(`"Объём, тыс. т/сутки";"${selectDemoScenario(config).volume}"`))
      assert.match(report, /не наблюдения, не прогноз/)
    }
  }
})
