import fixtures from './demo-fixtures.json' with { type: 'json' }

export type Direction = 'inbound' | 'outbound'
export type Scenario = 'base' | 'capacity' | 'route'
export type ForecastConfig = { direction: Direction; scenario: Scenario }

export const DEFAULT_CONFIG: ForecastConfig = { direction: 'inbound', scenario: 'base' }
export const DIRECTIONS: Direction[] = ['inbound', 'outbound']
export const DIRECTION_LABELS: Record<Direction, string> = {
  inbound: 'В Калининградскую область из основной части РФ',
  outbound: 'Из Калининградской области в основную часть РФ',
}
export const SCENARIOS: { id: Scenario; short: string; title: string; description: string }[] = [
  { id: 'base', short: 'Базовый', title: 'Базовый пример', description: 'Синтетическая исходная ситуация' },
  { id: 'capacity', short: 'А. Мощность', title: 'А. Мощность', description: 'Иллюстрация, действие командой не утверждено' },
  { id: 'route', short: 'Б. Маршрут', title: 'Б. Новый маршрут', description: 'Иллюстрация, действие командой не утверждено' },
]

export const formatNumber = (value: number) => value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
export const signed = (value: number, suffix = '%') => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value)}${suffix}`

export function selectDemoScenario(config: ForecastConfig) {
  if (!DIRECTIONS.includes(config.direction) || !SCENARIOS.some(({ id }) => id === config.scenario)) {
    throw new Error('Неизвестный синтетический пример')
  }
  return fixtures[config.direction][config.scenario]
}
