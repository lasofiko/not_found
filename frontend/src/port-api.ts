import { z } from 'zod'
import { getApiBase, requestJson } from './api.ts'

const tonnes = z.number().finite().nonnegative()
const percent = z.number().finite().nonnegative().nullable()
const bounds = z.object({ lower: tonnes, upper: tonnes })

export const DISPLAY_LABELS = {
  primary: 'ОСНОВНОЙ ПРОГНОЗ',
  shown: 'ПРОГНОЗ',
  shown_with_warning: 'ПРОГНОЗ · С ОГОВОРКОЙ',
  history_only: 'ТОЛЬКО ИСТОРИЯ',
} as const

const display = z.enum(['primary', 'shown', 'shown_with_warning', 'history_only'])

const quality = z.object({
  selected_model: z.string().min(1), model_version: z.string().min(1),
  selection: z.object({ period: z.string(), selected_WAPE: percent, naive_WAPE: percent }),
  holdout: z.object({ period: z.string(), selected_WAPE: percent, naive_WAPE: percent, observations: z.number().int().nonnegative() }),
  blind_2026: z.array(z.object({ period: z.string(), selected_APE: percent, naive_APE: percent, reference_status: z.string() })).length(2),
  blind_verdict: z.enum(['better_than_naive_in_both', 'worse_than_naive_in_both', 'mixed']),
})

export const portCatalogSchema = z.object({
  status: z.literal('ready'), version: z.string(), primary_target: z.string(),
  targets: z.array(z.object({
    target_id: z.string(), role: z.string(), title: z.string(), display, note: z.string(),
    expected_model_version: z.string(), port: z.string(), cargo_group: z.string(),
    model_connected: z.boolean(), quality,
  })).min(1),
  warnings: z.array(z.string()),
}).refine(c => c.targets.some(t => t.target_id === c.primary_target && t.display === 'primary'))
export type PortCatalog = z.infer<typeof portCatalogSchema>

const point = z.object({
  status: z.literal('forecast'),
  period: z.string().regex(/^20\d{2}-(0[1-9]|1[0-2])$/), value: tonnes, experimental: z.boolean(),
  interval: z.object({ '80': bounds, '95': bounds }),
}).refine(p => p.interval['95'].lower <= p.interval['80'].lower && p.interval['80'].lower <= p.value &&
  p.value <= p.interval['80'].upper && p.interval['80'].upper <= p.interval['95'].upper)

export const portForecastSchema = z.object({
  status: z.literal('ready'), target_id: z.string(), title: z.string(), role: z.string(), display, note: z.string(),
  port: z.string(), cargo_group: z.string(), unit: z.literal('tonnes'), granularity: z.literal('month'),
  selected_model: z.string(), model_version: z.string(), data_version: z.string(), forecast_origin: z.string(),
  result_type: z.enum(['forecast', 'history']),
  training_period: z.object({ start: z.string(), end: z.string() }),
  training_data_available_by: z.string(), generated_at: z.string(),
  provenance: z.object({ source_ids: z.array(z.string()).optional(),
    sources: z.array(z.object({ source_id: z.string() })).optional() }),
  history: z.array(z.object({ period: z.string(), value: tonnes, status: z.string() })).min(1),
  predictions: z.array(point), quality, warnings: z.array(z.string()),
}).refine(f => f.display === 'history_only'
  ? f.predictions.length === 0 && f.result_type === 'history'
  : f.result_type === 'forecast')
export type PortForecast = z.infer<typeof portForecastSchema>

export async function getPortCatalog(signal?: AbortSignal): Promise<PortCatalog> {
  const base = await getApiBase(signal)
  return requestJson(base + '/port-forecasts', value => portCatalogSchema.parse(value), signal)
}

export async function getPortForecast(targetId: string, endPeriod: string, signal?: AbortSignal): Promise<PortForecast> {
  const base = await getApiBase(signal)
  const url = `${base}/port-forecasts/${encodeURIComponent(targetId)}?end_period=${encodeURIComponent(endPeriod)}`
  return requestJson(url, value => {
    const result = portForecastSchema.parse(value)
    if (result.target_id !== targetId) throw new Error('API вернул прогноз другой цели.')
    return result
  }, signal)
}

/** Строки графика: история и прогноз на одной оси месяцев; интервал 80% — для полосы. */
export function chartRows(forecast: PortForecast, historyMonths = 24) {
  const history = forecast.history.slice(-historyMonths).map(h => ({ period: h.period, history: h.value }))
  const last = history.at(-1)
  const future = forecast.predictions.map(p => ({
    period: p.period, forecast: p.value, band: [p.interval['80'].lower, p.interval['80'].upper] as [number, number],
  }))
  if (last && future.length) Object.assign(last, { forecast: last.history })
  return [...history, ...future]
}

export function verdictText(verdict: PortCatalog['targets'][number]['quality']['blind_verdict']) {
  if (verdict === 'better_than_naive_in_both') return 'В обоих сравнениях 2026 года ошибка меньше naive'
  if (verdict === 'worse_than_naive_in_both') return 'В обоих сравнениях 2026 года ошибка больше naive'
  return 'Результат 2026 года смешанный: зависит от периода'
}
