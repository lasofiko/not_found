import { z } from 'zod'

const number = z.number().finite().nonnegative()
const bounds = z.object({ lower: number, upper: number })
const point = z.object({
  period: z.string().regex(/^20\d{2}$/), period_granularity: z.literal('year'),
  horizon_years: z.number().int().min(1).max(7), value: number,
  interval: z.object({ '80': bounds, '95': bounds }),
}).refine(p => p.interval['95'].lower <= p.interval['80'].lower && p.interval['80'].lower <= p.value &&
  p.value <= p.interval['80'].upper && p.interval['80'].upper <= p.interval['95'].upper)
const score = z.object({ MAE: number.nullable(), WAPE: number.nullable(), observations: z.number().int().nonnegative() })
const stages = z.object({ development: score, holdout: score })
export const forecastSchema = z.object({
  status: z.literal('ready'), result_type: z.literal('forecast'), target_id: z.literal('ust_luga_kaliningrad_ferry'),
  target_name: z.string().min(1), region: z.literal('kaliningrad'), direction: z.enum(['inbound', 'outbound']),
  mode: z.literal('sea'), unit: z.literal('tonnes'), geography: z.object({ origin: z.string(), destination: z.string(), scope: z.string(), route_id: z.string() }),
  training_period: z.object({ start: z.string(), end: z.string() }), forecast_origin: z.number().int(),
  observations: z.number().int().positive(), selected_model: z.string(),
  data_version: z.string().min(1), model_version: z.string().min(1), generated_at: z.string(),
  predictions: z.array(point).min(1),
  provenance: z.object({ source_ids: z.array(z.string()).min(1), sources: z.array(z.object({ source_id: z.string(), organization: z.string(), url: z.string().url() })).min(1) }).passthrough(),
  metrics: z.object({ selected: stages, baseline: stages, selection_period: z.string(), holdout_period: z.string(), evaluation_horizon_years: z.number(), methodological_status: z.string() }),
  warnings: z.array(z.string()),
}).refine(data => data.predictions.every((p, index) => p.period === String(2024 + index) && p.horizon_years === index + 1))
export type Forecast = z.infer<typeof forecastSchema>

export const scenarioSchema = z.array(z.object({
  id: z.string(), label: z.string().nullable(), name: z.string(), mechanism: z.string(),
  calculation_status: z.string(), limitation: z.string(),
}))
export type ScenarioDefinition = z.infer<typeof scenarioSchema>[number]

export async function requestJson<T>(url: string, parse: (value: unknown) => T, signal?: AbortSignal, timeout = 10000): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, timeout)
  try {
    const response = await fetch(url, { signal: controller.signal, cache: 'no-store' })
    const data: unknown = await response.json()
    if (!response.ok) {
      const error = z.object({ message: z.string() }).safeParse(data)
      throw new Error(error.success ? error.data.message : `Ошибка API (${response.status})`)
    }
    return parse(data)
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Запрос отменён', 'AbortError')
    if (timedOut) throw new Error('Время ожидания API истекло. Повторите запрос.')
    if (error instanceof z.ZodError || error instanceof SyntaxError) throw new Error('Сервер вернул некорректный результат.')
    if (error instanceof TypeError) throw new Error('Backend недоступен. Проверьте подключение и повторите запрос.')
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

export async function getApiBase(signal?: AbortSignal) {
  const config = await requestJson('/config.json', value => z.object({ apiBase: z.string().min(1) }).parse(value), signal)
  const url = new URL(config.apiBase, window.location.origin)
  if (!['http:', 'https:'].includes(url.protocol) || config.apiBase.startsWith('//') || url.search || url.hash) throw new Error('Некорректный адрес API')
  return url.href.replace(/\/+$/, '')
}
