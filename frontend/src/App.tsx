import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDownRight,
  ArrowLeftRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Box,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Clock3,
  Database,
  Download,
  Info,
  Layers3,
  LayoutDashboard,
  Lightbulb,
  MapPin,
  Menu,
  Moon,
  RotateCcw,
  Route,
  SlidersHorizontal,
  Sun,
  Target,
  TrendingUp,
  Truck,
  User,
  Warehouse,
  X,
} from 'lucide-react'
import { DynamicsChart, HypothesisChart, VolumeChart } from './ForecastCharts'
import {
  DEFAULT_CONFIG,
  DIRECTIONS,
  SCENARIOS,
  TRANSPORTS,
  calculateForecast,
  formatNumber,
  signed,
  type ForecastConfig,
  type Scenario,
} from './dashboard-data'
import './App.css'

const copyConfig = (config: ForecastConfig): ForecastConfig => ({ ...config, options: { ...config.options } })
type Theme = 'dark' | 'light'

const navItems = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'forecast', label: 'Прогноз', icon: BarChart3 },
  { id: 'scenarios', label: 'Сценарии', icon: Layers3 },
  { id: 'analytics', label: 'Аналитика', icon: TrendingUp },
  { id: 'data', label: 'Данные', icon: Database },
  { id: 'about', label: 'О проекте', icon: Info },
]

function Delta({ value, suffix = '%', inverse = false }: { value: number; suffix?: string; inverse?: boolean }) {
  const positive = value >= 0
  const favorable = inverse ? !positive : positive
  return (
    <span className={`delta ${favorable ? 'delta-good' : 'delta-warn'}`}>
      {signed(value, suffix)}
    </span>
  )
}

function NeonWaves() {
  const paths = Array.from({ length: 8 }, (_, index) => {
    const y = 18 + index * 7
    return `M0 ${y} C90 ${y - 28}, 155 ${y + 26}, 245 ${y} S405 ${y - 25}, 520 ${y + 2}`
  })
  return (
    <svg className="neon-waves" viewBox="0 0 520 100" aria-hidden="true">
      {paths.map((path, index) => <path key={path} d={path} style={{ opacity: 0.16 + index * 0.055 }} />)}
    </svg>
  )
}

export default function App() {
  const [draft, setDraft] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [applied, setApplied] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [paramsOpen, setParamsOpen] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('overview')
  const [theme, setTheme] = useState<Theme>('dark')
  const [notice, setNotice] = useState('Исходный демо-расчёт')
  const result = useMemo(() => calculateForecast(applied), [applied])
  const dirty = JSON.stringify(draft) !== JSON.stringify(applied)
  const scenarioTitle = SCENARIOS.find((scenario) => scenario.id === applied.scenario)?.title ?? 'Базовый сценарий'

  useEffect(() => {
    const closeOverlays = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setNavOpen(false)
        setProfileOpen(false)
      }
    }
    window.addEventListener('keydown', closeOverlays)
    return () => window.removeEventListener('keydown', closeOverlays)
  }, [])

  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    document.documentElement.style.background = theme === 'dark' ? '#030C1B' : '#EDF3F6'
    document.body.style.background = theme === 'dark' ? '#030C1B' : '#EDF3F6'
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#030C1B' : '#EDF3F6')
  }, [theme])

  const calculate = () => {
    setApplied(copyConfig(draft))
    setNotice('Прогноз обновлён')
    setParamsOpen(false)
  }

  const reset = () => {
    setDraft(copyConfig(DEFAULT_CONFIG))
    setApplied(copyConfig(DEFAULT_CONFIG))
    setNotice('Исходные параметры восстановлены')
  }

  const exportReport = () => {
    const rows = [
      ['Прогноз грузовых перевозок', ''],
      ['Направление', applied.direction],
      ['Транспорт', applied.transport],
      ['Сценарий', scenarioTitle],
      ['Объём груза к 2030, тыс. тонн/сутки', formatNumber(result.volume)],
      ['Изменение объёма к 2025, %', String(result.volumeDelta)],
      [`Время, ${result.timeUnit}`, formatNumber(result.time)],
      ['Доступность городов, %', `+${result.access}`],
      [`Загрузка терминала (${applied.direction}), %`, String(result.terminal)],
      ['', ''],
      ['Примечание', 'Все числа иллюстративные'],
    ]
    const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(';')).join('\n')}`
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `Прогноз_${applied.direction}_2030.csv`
    link.click()
    URL.revokeObjectURL(url)
    setNotice('Отчёт CSV выгружен')
  }

  const toggleOption = (option: keyof ForecastConfig['options']) => {
    setDraft((current) => ({ ...current, options: { ...current.options, [option]: !current.options[option] } }))
  }

  const insight = applied.scenario === 'route'
    ? 'Новый маршрут даёт наибольший прирост объёма и сокращает время в пути.'
    : applied.scenario === 'capacity'
      ? 'Дополнительная мощность ускоряет доставку без создания нового маршрута.'
      : 'Новый маршрут даёт +35% к грузу, но требует проверки пропускной способности.'

  return (
    <div className={`app-shell ${theme === 'light' ? 'light-theme' : ''}`}>
      {navOpen && <button className="nav-backdrop" aria-label="Закрыть навигацию" onClick={() => setNavOpen(false)} />}
      <aside className={`sidebar ${navOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <span className="brand-mark">nf</span>
          <span className="wordmark">Not <s>Found</s></span>
          <button className="sidebar-close" aria-label="Закрыть меню" onClick={() => setNavOpen(false)}><X size={20} /></button>
        </div>
        <nav className="main-nav" aria-label="Разделы дашборда">
          {navItems.map((item) => {
            const Icon = item.icon
            return (
              <a key={item.id} href={`#${item.id}`} className={activeNav === item.id ? 'active' : ''} onClick={() => { setActiveNav(item.id); setNavOpen(false) }}>
                <Icon size={19} aria-hidden="true" /><span>{item.label}</span>
              </a>
            )
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-orbit"><span /><span /><span /></div>
          <div className="sidebar-location"><MapPin size={16} /><span>Калининградская<br />агломерация</span></div>
          <small>Горизонт прогноза<br /><strong>2025–2030</strong></small>
        </div>
      </aside>

      <div className="main-shell">
        <main className="dashboard" id="overview">
          <header className={`topbar ${profileOpen ? 'profile-open' : ''}`}>
            <button className="mobile-menu" aria-label="Открыть навигацию" onClick={() => setNavOpen(true)}><Menu size={22} /></button>
            <div className="page-heading">
              <h1>Прогноз грузоперевозок</h1>
              <p>Калининградская агломерация <span>↔</span> 2025–2030</p>
            </div>
            <NeonWaves />
            <div className="topbar-actions">
              <div className="demo-badge"><span className="pulse-dot" />Демо-данные</div>
              <button
                className="theme-toggle"
                aria-label={theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'}
                aria-pressed={theme === 'light'}
                title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
                onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
              >
                {theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}
              </button>
              <button className="export-button" onClick={exportReport}><Download size={17} /><span>Экспорт отчёта</span></button>
              <div className="profile-wrap">
                <button className="profile-button" aria-label="Информация о пользователе" aria-expanded={profileOpen} onClick={() => setProfileOpen((open) => !open)}><User size={20} /></button>
                {profileOpen && <div className="profile-popover"><strong>Демо-пользователь</strong><span>Локальный просмотр</span><small>Вход и синхронизация не требуются</small></div>}
              </div>
            </div>
          </header>

          <section className="filter-section" aria-label="Параметры прогноза">
            <div className="quick-filters">
              <div className="filter-display">
                <CalendarDays size={20} /><div><span>Горизонт</span><strong>2030 год</strong></div>
              </div>
              <label className="filter-control">
                <ArrowLeftRight size={20} /><span className="filter-control-content"><span>Направление</span>
                  <select aria-label="Направление" id="direction" value={draft.direction} onChange={(event) => setDraft({ ...draft, direction: event.target.value as ForecastConfig['direction'] })}>
                    {DIRECTIONS.map((direction) => <option key={direction}>{direction}</option>)}
                  </select>
                </span><ChevronDown size={16} />
              </label>
              <label className="filter-control">
                <Truck size={20} /><span className="filter-control-content"><span>Вид транспорта</span>
                  <select aria-label="Вид транспорта" id="transport" value={draft.transport} onChange={(event) => setDraft({ ...draft, transport: event.target.value as ForecastConfig['transport'] })}>
                    {TRANSPORTS.map((transport) => <option key={transport}>{transport}</option>)}
                  </select>
                </span><ChevronDown size={16} />
              </label>
              <label className="filter-control">
                <Layers3 size={20} /><span className="filter-control-content"><span>Режим</span>
                  <select aria-label="Режим перевозок" id="mode" value={draft.mode} onChange={(event) => setDraft({ ...draft, mode: event.target.value as ForecastConfig['mode'] })}>
                    <option value="federal">В регионы РФ</option><option value="region">Внутри области</option>
                  </select>
                </span><ChevronDown size={16} />
              </label>
              <button className="calculate-button" onClick={calculate}>Рассчитать прогноз <ArrowRight size={19} /></button>
              <button className="reset-button" aria-label="Сбросить параметры" title="Сбросить параметры" onClick={reset}><RotateCcw size={18} /></button>
            </div>
            <button className="advanced-toggle" aria-expanded={paramsOpen} aria-controls="forecast-parameters" onClick={() => setParamsOpen((open) => !open)}>
              <SlidersHorizontal size={16} />Дополнительные условия и сценарий<ChevronDown className={paramsOpen ? 'rotate' : ''} size={16} />
            </button>
            <div id="forecast-parameters" className={`advanced-panel ${paramsOpen ? 'is-open' : ''}`}>
              <fieldset className="scenario-control">
                <legend>Сценарий развития</legend>
                <div className="scenario-options">
                  {SCENARIOS.map((scenario) => (
                    <label key={scenario.id} className={draft.scenario === scenario.id ? 'selected' : ''}>
                      <input type="radio" name="scenario" value={scenario.id} checked={draft.scenario === scenario.id} onChange={() => setDraft({ ...draft, scenario: scenario.id as Scenario })} />
                      <span className="scenario-radio" /><span><strong>{scenario.short}</strong><small>{scenario.description}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="options-group">
                <legend>Дополнительные условия</legend>
                <div className="checkbox-grid">
                  {([['newRoute', 'Новый маршрут'], ['frequency', 'Увеличение частоты'], ['terminal', 'Грузовой терминал']] as const).map(([value, label]) => (
                    <label className="checkbox-label" key={value}>
                      <input type="checkbox" checked={draft.options[value]} onChange={() => toggleOption(value)} />
                      <span className="custom-checkbox"><Check size={13} /></span>{label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="advanced-actions"><button className="button-primary" onClick={calculate}>Применить параметры <ArrowRight size={17} /></button><button className="button-secondary" onClick={reset}><RotateCcw size={16} />Сбросить</button></div>
            </div>
            <div className="filter-status" role="status"><span className={dirty ? 'status-dot is-dirty' : 'status-dot'} />{dirty ? 'Параметры изменены — нажмите «Рассчитать прогноз»' : notice}<span className="current-scenario">{scenarioTitle}</span></div>
          </section>

          <section className="kpi-grid" id="forecast" aria-label="Ключевые показатели">
            <article className="kpi-card kpi-primary">
              <div className="kpi-top"><span><Box size={20} />Объём груза к 2030</span></div>
              <div className="kpi-main"><div className="kpi-value">{formatNumber(result.volume)} <small>тыс. т/сутки</small></div><Delta value={result.volumeDelta} /></div>
              <div className="kpi-bottom"><ArrowUpRight size={15} />к 2025 году</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><Clock3 size={20} />{applied.mode === 'region' ? 'Время поездки' : 'Время доставки'}</span></div>
              <div className="kpi-main"><div className="kpi-value">{formatNumber(result.time)} <small>{result.timeUnit}</small></div><Delta value={result.timeDelta} inverse /></div>
              <div className="kpi-bottom"><ArrowDownRight size={15} />к 2025 году · {applied.mode === 'region' ? 'внутри области' : 'в регионы РФ'}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><MapPin size={20} />Доступность городов</span></div>
              <div className="kpi-main"><div className="kpi-value">+{result.access}<small>%</small></div><Delta value={result.access} /></div>
              <div className="kpi-bottom"><ArrowUpRight size={15} />к 2025 году</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><Warehouse size={20} />Загрузка терминала</span></div>
              <div className="kpi-main"><div className="kpi-value">{result.terminal}<small>%</small></div><Delta value={result.terminalDelta} suffix=" п.п." /></div>
              <div className="kpi-bottom"><ArrowUpRight size={15} />{applied.direction} · к 2025 году</div>
            </article>
          </section>

          <section className="charts-grid" id="analytics" aria-label="Аналитика прогноза">
            <article className="chart-card">
              <div className="chart-heading"><BarChart3 size={19} /><h2>Объём груза к 2030: {scenarioTitle.toLowerCase()}</h2></div>
              <VolumeChart data={result.stacked} light={theme === 'light'} />
            </article>
            <article className="chart-card">
              <div className="chart-heading"><TrendingUp size={19} /><h2>Динамика по годам, 2025–2030</h2></div>
              <DynamicsChart data={result.dynamics} light={theme === 'light'} />
            </article>
            <article className="chart-card">
              <div className="chart-heading"><Layers3 size={19} /><h2>Объём груза и время доставки</h2></div>
              <HypothesisChart data={result.hypotheses} timeUnit={result.timeUnit} light={theme === 'light'} />
            </article>
          </section>

          <section className="scenario-layout" id="scenarios">
            <div className="hypotheses-panel">
              <div className="panel-heading"><Target size={20} /><h2>Сравнение гипотез действий</h2></div>
              <div className="hypothesis-cards">
                <article className="hypothesis-card">
                  <div className="hypothesis-title"><span>А</span><h3>Больше мощности</h3></div>
                  <div className="hypothesis-value">{formatNumber(result.hypotheses[1].volume)} <small>тыс. т/сутки</small><Delta value={18} /></div>
                  <p><CheckCircle2 size={15} />Увеличение мощности терминалов · время −22%</p>
                </article>
                <article className="hypothesis-card">
                  <div className="hypothesis-title"><span>Б</span><h3>Новый маршрут</h3></div>
                  <div className="hypothesis-value">{formatNumber(result.hypotheses[2].volume)} <small>тыс. т/сутки</small><Delta value={35} /></div>
                  <p><CheckCircle2 size={15} />Обход перегруженных участков · время −28%</p>
                </article>
              </div>
            </div>
            <article className="insight-panel">
              <div className="panel-heading"><Lightbulb size={20} /><h2>Что меняет выбор</h2></div>
              <p>{insight}</p>
              <div className="insight-bottom"><span>{applied.direction}</span><Truck size={58} strokeWidth={1.2} /></div>
            </article>
          </section>

          <section className="info-grid" id="data">
            <article className="info-card limitations-card">
              <div className="panel-heading"><Info size={19} /><h2>Данные и ограничения</h2></div>
              <ul>
                <li><strong>Груз:</strong> авто 2001–2024, ж/д 2001–2022, частично море и авиация.</li>
                <li><strong>Время:</strong> расчётное по расписаниям; фактических замеров нет.</li>
                <li><strong>Доступность:</strong> 9 автостанций и электрички; длинной истории нет.</li>
                <li><strong>Загрузка:</strong> показатели терминала; ряда по маршрутам нет.</li>
              </ul>
            </article>
            <article className="info-card guide-card">
              <div className="panel-heading"><CircleHelp size={19} /><h2>Как читать прогноз</h2></div>
              <p><strong>Вход:</strong> направление, транспорт, режим и сценарий.</p>
              <p><strong>Выход:</strong> объём, время, доступность и загрузка.</p>
              <p>Стрелка показывает изменение к 2025 году.</p>
            </article>
            <article className="info-card questions-card">
              <div className="panel-heading"><CheckCircle2 size={19} /><h2>Что проверить дополнительно</h2></div>
              <p>Сезонность, тарифы, реальная загрузка маршрутов, контейнеризация и пропускная способность.</p>
              <span>Все числа иллюстративные</span>
            </article>
          </section>

          <section className="ai-note" id="about">
            <Info size={21} /><div><h2>О проекте</h2><p>«Инструмент прогноза грузовых перевозок» — демонстрационный проект команды Not Found для Калининградской агломерации. Он помогает сравнивать сценарии развития транспортной сети до 2030 года: оценивать объём грузов, время доставки, доступность городов и загрузку терминалов. Интерфейс работает на иллюстративных данных и предназначен для обсуждения решений, а не для официального прогнозирования.</p></div><Route size={28} />
          </section>
        </main>

        <footer className="site-footer"><span>© 2025 Not Found · Калининградская агломерация</span><span>Сценарный инструмент · v2.0</span></footer>
      </div>
    </div>
  )
}
