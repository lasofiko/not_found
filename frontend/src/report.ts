import { DIRECTION_LABELS, SCENARIOS, selectDemoScenario } from './dashboard-data.ts'
import type { ForecastConfig } from './dashboard-data.ts'
import type { Forecast } from './api.ts'

export function createForecastReport(result: Forecast): string {
  const rows = [['period', 'value', 'unit', 'region', 'origin', 'destination', 'direction', 'mode', 'status', 'data_version', 'model_version', 'source_ids', 'lower_80', 'upper_80', 'lower_95', 'upper_95']]
  for (const point of result.predictions) {
    rows.push([point.period, String(point.value), result.unit, result.region, result.geography.origin,
      result.geography.destination, result.direction, result.mode, result.result_type, result.data_version,
      result.model_version, result.provenance.source_ids.join('|'), String(point.interval['80'].lower),
      String(point.interval['80'].upper), String(point.interval['95'].lower), String(point.interval['95'].upper)])
  }
  return '\uFEFF' + rows.map(row => row.map(cell => '"' + cell.replaceAll('"', '""') + '"').join(';')).join('\r\n') + '\r\n'
}

export function createDemoReport(config: ForecastConfig): string {
  const result = selectDemoScenario(config)
  const scenario = SCENARIOS.find(({ id }) => id === config.scenario)!
  const rows = [
    ['FloCast', 'Синтетический пример интерфейса'],
    ['Статус', result.status],
    ['Происхождение', result.source],
    ['Направление', config.direction],
    ['Описание направления', DIRECTION_LABELS[config.direction]],
    ['Вид транспорта', 'sea'],
    ['Пример', scenario.title],
    ['Период', '2030'],
    ['Объём, тыс. т/сутки', String(result.volume)],
    ['Изменение объёма к синтетическому 2025, %', String(result.volumeDelta)],
    ['Время, ч', String(result.time)],
    ['Изменение времени к синтетическому 2025, %', String(result.timeDelta)],
    ['Доступность городов: условное изменение, %', String(result.access)],
    ['Загрузка терминала, %', String(result.terminal)],
    ['Изменение загрузки, п.п.', String(result.terminalDelta)],
    ['Ограничения', 'Все числа вымышлены; не наблюдения, не прогноз и не оценка эффекта инвестиции'],
  ]
  return '\uFEFF' + rows.map((row) => row.map((cell) => '"' + cell.replaceAll('"', '""') + '"').join(';')).join('\r\n') + '\r\n'
}
