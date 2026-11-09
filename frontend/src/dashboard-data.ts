export type Mode = 'region' | 'federal'
export type Direction = 'Черняховск' | 'Балтийск' | 'Мамоново' | 'Советск'
export type Transport = 'Автомобильный' | 'Железнодорожный' | 'Морской' | 'Смешанный'
export type Scenario = 'base' | 'capacity' | 'route'

export type ForecastConfig = {
  mode: Mode
  direction: Direction
  transport: Transport
  scenario: Scenario
  options: {
    newRoute: boolean
    frequency: boolean
    terminal: boolean
  }
}

export const DEFAULT_CONFIG: ForecastConfig = {
  mode: 'federal',
  direction: 'Балтийск',
  transport: 'Смешанный',
  scenario: 'base',
  options: { newRoute: false, frequency: false, terminal: false },
}

export const DIRECTIONS: Direction[] = ['Черняховск', 'Балтийск', 'Мамоново', 'Советск']
export const TRANSPORTS: Transport[] = ['Автомобильный', 'Железнодорожный', 'Морской', 'Смешанный']

export const SCENARIOS: { id: Scenario; short: string; title: string; description: string }[] = [
  { id: 'base', short: 'Базовый', title: 'Базовый сценарий', description: 'Текущая инфраструктура' },
  { id: 'capacity', short: 'А. Больше мощности', title: 'А. Больше мощности', description: 'Чаще рейсы, выше пропускная способность' },
  { id: 'route', short: 'Б. Новый маршрут', title: 'Б. Новый маршрут', description: 'Новая связь между городами' },
]

const directionFactor: Record<Direction, number> = {
  Черняховск: 0.88,
  Балтийск: 1,
  Мамоново: 0.94,
  Советск: 0.9,
}

const transportFactor: Record<Transport, number> = {
  Автомобильный: 0.96,
  Железнодорожный: 1.04,
  Морской: 1.08,
  Смешанный: 1,
}

export const round1 = (value: number) => Math.round(value * 10) / 10
export const formatNumber = (value: number) => value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
export const signed = (value: number, suffix = '%') => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value)}${suffix}`

export function calculateForecast(config: ForecastConfig) {
  const direction = directionFactor[config.direction]
  const transport = transportFactor[config.transport]
  const modeVolume = config.mode === 'region' ? 0.82 : 1
  const scenarioVolume = config.scenario === 'capacity' ? 1.18 : config.scenario === 'route' ? 1.35 : 1
  const scenarioTime = config.scenario === 'capacity' ? 0.78 : config.scenario === 'route' ? 0.72 : 1
  const optionVolume = (config.options.newRoute ? 1.08 : 1) * (config.options.frequency ? 1.05 : 1) * (config.options.terminal ? 1.03 : 1)
  const optionTime = (config.options.newRoute ? 0.95 : 1) * (config.options.frequency ? 0.97 : 1) * (config.options.terminal ? 0.94 : 1)
  const baseVolume2025 = 12 * direction * modeVolume
  const baseVolume2030 = 14.5 * direction * transport * modeVolume
  const volume = round1(baseVolume2030 * scenarioVolume * optionVolume)
  const volumeDelta = Math.round((volume / baseVolume2025 - 1) * 100)
  const time = round1((config.mode === 'region' ? 46 : 3.2) * scenarioTime * optionTime)
  const baseTime2025 = config.mode === 'region' ? 53 : 3.8
  const timeDelta = Math.round((time / baseTime2025 - 1) * 100)
  const scenarioAccess = config.scenario === 'capacity' ? 1.25 : config.scenario === 'route' ? 1.75 : 1
  const access = Math.round(12 * direction * scenarioAccess * (config.options.newRoute ? 1.2 : 1))
  const terminal = Math.min(96, Math.round(78 * (0.9 + direction * 0.1) + (config.options.terminal ? 8 : 0) + (config.scenario === 'capacity' ? 4 : 0)))
  const terminalDelta = terminal - 72

  const modeTimeUnit = config.mode === 'region' ? 'мин' : 'ч'
  const baseTime2030 = config.mode === 'region' ? 46 : 3.2

  const shares = config.transport === 'Автомобильный'
    ? { auto: 0.66, rail: 0.2, sea: 0.14 }
    : config.transport === 'Железнодорожный'
      ? { auto: 0.42, rail: 0.43, sea: 0.15 }
      : config.transport === 'Морской'
        ? { auto: 0.4, rail: 0.2, sea: 0.4 }
        : { auto: 0.565, rail: 0.262, sea: 0.173 }

  const stacked = [
    {
      year: '2025',
      Авто: round1(baseVolume2025 * 0.6),
      'Ж/Д': round1(baseVolume2025 * 0.25),
      Море: round1(baseVolume2025 * 0.15),
    },
    {
      year: '2030',
      Авто: round1(volume * shares.auto),
      'Ж/Д': round1(volume * shares.rail),
      Море: round1(volume * shares.sea),
    },
  ]

  const years = [2025, 2026, 2027, 2028, 2029, 2030]
  const dynamics = years.map((year, index) => {
    const t = index / 5
    return {
      year: String(year),
      'Базовый': round1(baseVolume2025 + (baseVolume2030 - baseVolume2025) * t),
      'А. Мощность': round1(baseVolume2025 + (baseVolume2030 * 1.18 - baseVolume2025) * t),
      'Б. Новый маршрут': round1(baseVolume2025 + (baseVolume2030 * 1.35 - baseVolume2025) * t),
    }
  })

  const hypotheses = [
    { name: 'Базовый', volume: round1(baseVolume2030), time: round1(baseTime2030), unit: modeTimeUnit },
    { name: 'А. Мощность', volume: round1(baseVolume2030 * 1.18), time: round1(baseTime2030 * 0.78), unit: modeTimeUnit },
    { name: 'Б. Новый маршрут', volume: round1(baseVolume2030 * 1.35), time: round1(baseTime2030 * 0.72), unit: modeTimeUnit },
  ]

  return {
    volume,
    volumeDelta,
    time,
    timeDelta,
    timeUnit: modeTimeUnit,
    access,
    terminal,
    terminalDelta,
    stacked,
    dynamics,
    hypotheses,
  }
}
