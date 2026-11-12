import { z } from 'zod'
import { getApiBase, requestJson } from './api'

const finite = z.number().finite()
const mass = finite.nonnegative()
export const passportSchema = z.object({
  title: z.string(), definition: z.string(), value: z.unknown(), unit: z.string(), period: z.string(),
  geography: z.string(), direction: z.string(), result_type: z.string(), source: z.string(),
  source_url: z.string().nullable().optional(), original_unit: z.string().nullable().optional(),
  conversion: z.string().nullable().optional(), formula: z.string().nullable().optional(),
  data_version: z.string().nullable().optional(), model_version: z.string().nullable().optional(),
  forecast_origin: z.number().nullable().optional(), calculated_at: z.string(),
  machine_validation: z.unknown(), human_review: z.unknown().nullable().optional(), limitations: z.array(z.string()),
})
export type Passport = z.infer<typeof passportSchema>
const sourceSchema = z.object({ source_id: z.string(), organization: z.string().optional(), url: z.string().nullable().optional() }).passthrough()
export const profileSchema = z.object({
  status: z.enum(['ready', 'partial', 'not_connected']), unit: z.literal('tonnes'),
  model_version: z.string().nullable(), data_version: z.string().nullable(), forecast_origin: z.number().nullable(),
  rows: z.array(z.object({ period: z.string(), result_type: z.string(), inbound: mass.nullable(), outbound: mass.nullable(),
    total: mass.nullable(), inbound_share: finite.nullable(), outbound_share: finite.nullable(),
    difference_tonnes: finite.nullable(), inbound_outbound_ratio: finite.nullable() })),
  sources: z.array(sourceSchema), warnings: z.array(z.string()), passports: z.record(z.string(), passportSchema),
})
export type DirectionProfile = z.infer<typeof profileSchema>
const variantSchema = z.object({
  id: z.enum(['baseline', 'reserve_ferry', 'additional_ferry']), label: z.string(), name: z.string(),
  demand_tonnes: mass, capacity_tonnes: mass, capacity_delta_tonnes: mass, scenario_capacity_tonnes: mass,
  scenario_shortfall_tonnes: mass, shortfall_reduction_tonnes: mass, scenario_spare_capacity_tonnes: mass,
  coverage_share: finite.nullable(), assumption_source: z.string(), status: z.literal('scenario'),
})
export const snapshotSchema = z.object({
  comparison_id: z.string().min(1), created_at: z.string(), expires_at: z.string(), status: z.literal('scenario'), unit: z.literal('tonnes'),
  context: z.object({ region: z.string(), target_id: z.string(), route: z.string(), scope: z.string(), mode: z.string(),
    direction: z.enum(['inbound', 'outbound']), period: z.string(), granularity: z.string(),
    model_version: z.string().nullable(), data_version: z.string().nullable(), forecast_origin: z.number().nullable() }),
  inputs: z.record(z.string(), z.unknown()), demand_tonnes: mass, variants: z.array(variantSchema).length(3),
  thresholds: z.object({ required_delta_tonnes: mass, grid: z.object({ demand_values: z.array(mass).max(15), delta_values: z.array(mass).max(15),
    shortfalls: z.array(z.array(mass).max(15)).max(15), boundary_delta_tonnes: z.array(mass).max(15) }) }),
  passports: z.record(z.string(), passportSchema), sources: z.array(sourceSchema), warnings: z.array(z.string()),
  forecast_basis: z.unknown().nullable(), direction_profile: profileSchema.nullable(),
}).superRefine((value, ctx) => {
  const grid = value.thresholds.grid
  if (grid.shortfalls.length !== grid.delta_values.length || grid.shortfalls.some(row => row.length !== grid.demand_values.length) || grid.boundary_delta_tonnes.length !== grid.demand_values.length) {
    ctx.addIssue({ code: 'custom', message: 'Неверная размерность пороговой сетки' })
  }
  if (new Set(value.variants.map(v => v.id)).size !== 3 || value.variants.some(v => v.demand_tonnes !== value.demand_tonnes)) {
    ctx.addIssue({ code: 'custom', message: 'Варианты относятся к разной нагрузке' })
  }
})
export type ComparisonSnapshot = z.infer<typeof snapshotSchema>
export type ComparisonInput = {
  region: string; mode: 'sea'; direction: 'inbound' | 'outbound'; period: string;
  load_basis: 'direct' | 'forecast'; demand_tonnes?: number; forecast_multiplier?: number;
  demand_source: string; capacity_tonnes: number; capacity_source: string;
  actions: { reserve_ferry: { delta_tonnes: number; source: string }; additional_ferry: { delta_tonnes: number; source: string } };
  include_directions: boolean; grid_size: number;
}
export const coverageSchema = z.object({ checked_at: z.string(), warnings: z.array(z.string()), rows: z.array(z.object({
  id: z.string(), territory: z.string(), object: z.string(), metric: z.string(), direction: z.string().nullable(),
  mode: z.string().nullable(), frequency: z.string(), period: z.string(), unit: z.string(), value_status: z.string(),
  stages: z.object({ source_registered: z.boolean().nullable(), original_available: z.boolean().nullable(),
    extracted: z.boolean().nullable(), series_checked: z.boolean().nullable(), model_exists: z.boolean().nullable(),
    model_connected: z.boolean().nullable(), ui_available: z.boolean().nullable() }),
  source: sourceSchema.nullable(), limitations: z.array(z.string()), checked_at: z.string(), forecast_available: z.boolean(),
  evidence_notes: z.array(z.string()).optional(),
})) })
export type Coverage = z.infer<typeof coverageSchema>
export const askSchema = z.object({ comparison_id: z.string(), status: z.string(), answer: z.string(), evidence_refs: z.array(z.string()) })
export const analysisSchema = z.object({ comparison_id: z.string(), status: z.enum(['complete', 'partial', 'unavailable']),
  agents: z.array(z.object({ agent_id: z.string(), status: z.string(), findings: z.array(z.string()), evidence_refs: z.array(z.string()),
    assumptions: z.array(z.string()), limitations: z.array(z.string()), questions_for_user: z.array(z.string()) })),
  sections: z.array(z.object({ title: z.string(), items: z.array(z.string()) })), warnings: z.array(z.string()),
})
export type Analysis = z.infer<typeof analysisSchema>

export async function comparisonRequest<T>(path: string, parse: (value: unknown) => T, signal: AbortSignal, body?: unknown, timeout = 15000): Promise<T> {
  const base = await getApiBase(signal)
  return requestJson(base + path, parse, signal, timeout, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
}

export function safeSourceUrl(value: string | null | undefined) {
  if (!value) return null
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null } catch { return null }
}

export async function comparisonExport(id: string, format: 'html' | 'csv' | 'json', signal: AbortSignal) {
  const base = await getApiBase(signal)
  const controller = new AbortController()
  const cancel = () => controller.abort()
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) controller.abort()
  const timer = setTimeout(cancel, 15000)
  try {
    const response = await fetch(`${base}/comparisons/${encodeURIComponent(id)}/export?format=${format}`, { signal: controller.signal, cache: 'no-store' })
    if (!response.ok) {
      const result = await response.json().catch(() => null)
      throw new Error(result?.error?.message ?? result?.message ?? 'Не удалось подготовить отчёт. Выполните сравнение заново.')
    }
    return await response.blob()
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel) }
}
