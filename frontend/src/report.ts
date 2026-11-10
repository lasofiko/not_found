import { DIRECTION_LABELS, SCENARIOS, selectDemoScenario } from './dashboard-data.ts'
import type { ForecastConfig } from './dashboard-data.ts'

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
