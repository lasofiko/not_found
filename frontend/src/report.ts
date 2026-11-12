import { DIRECTION_LABELS, SCENARIOS, selectDemoScenario } from './dashboard-data.ts'
import type { ForecastConfig } from './dashboard-data.ts'
import type { Forecast } from './api.ts'

export function createForecastReport(result: Forecast): string {
  const rows = [['period', 'value', 'unit', 'region', 'origin', 'destination', 'direction', 'mode', 'status', 'data_version', 'model_version', 'source_ids', 'lower_80', 'upper_80', 'lower_95', 'upper_95',
    'target_id', 'target_name', 'route_id', 'scope', 'period_granularity', 'horizon_years', 'forecast_origin', 'training_start', 'training_end', 'generated_at', 'selected_model', 'observations',
    'sources_json', 'warnings_json', 'provenance_json', 'metrics_json']]
  for (const point of result.predictions) {
    rows.push([point.period, String(point.value), result.unit, result.region, result.geography.origin,
      result.geography.destination, result.direction, result.mode, result.result_type, result.data_version,
      result.model_version, result.provenance.source_ids.join('|'), String(point.interval['80'].lower),
      String(point.interval['80'].upper), String(point.interval['95'].lower), String(point.interval['95'].upper),
      result.target_id, result.target_name, result.geography.route_id, result.geography.scope, point.period_granularity, String(point.horizon_years),
      String(result.forecast_origin), result.training_period.start, result.training_period.end, result.generated_at, result.selected_model, String(result.observations),
      JSON.stringify(result.provenance.sources), JSON.stringify(result.warnings), JSON.stringify(result.provenance), JSON.stringify(result.metrics)])
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
