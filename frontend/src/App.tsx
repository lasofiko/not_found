import { useEffect, useRef, useState } from 'react'
import { ArrowLeftRight, ArrowRight, BarChart3, Box, CalendarDays, CheckCircle2, ChevronDown, CircleHelp, Clock3, Database, Download, Info, Layers3, Lightbulb, MapPin, Menu, Moon, RotateCcw, SlidersHorizontal, Sparkles, Sun, Target, TrendingUp, Truck, User, Warehouse, X, LayoutDashboard } from 'lucide-react'
import { VolumeChart, DynamicsChart, HypothesisChart } from './ForecastCharts'
import { DIRECTION_LABELS, DIRECTIONS } from './dashboard-data'
import type { Direction } from './dashboard-data'
import { forecastSchema, getApiBase, requestJson, scenarioSchema } from './api'
import type { Forecast, ScenarioDefinition } from './api'
import { createForecastReport } from './report'
import { AiAssistant } from './AiAssistant'
import './App.css'

const navItems = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'forecast', label: 'Прогноз', icon: BarChart3 },
  { id: 'scenarios', label: 'Сценарии', icon: Layers3 },
  { id: 'analytics', label: 'Аналитика', icon: TrendingUp },
  { id: 'data', label: 'Данные', icon: Database },
  { id: 'about', label: 'О проекте', icon: Info },
]
type Draft = { direction: Direction; region: string; end_year: number }
const initial: Draft = { direction: 'inbound', region: 'kaliningrad', end_year: 2030 }
const formatNumber = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })

export default function App() {
  const [draft, setDraft] = useState<Draft>(initial)
  const [result, setResult] = useState<Forecast | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioDefinition[]>([])
  const [scenarioError, setScenarioError] = useState('')
  const [selectedScenario, setSelectedScenario] = useState('reserve_ferry')
  const [status, setStatus] = useState('Загрузка прогноза…')
  const [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(true)
  const [paramsOpen, setParamsOpen] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('overview')
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const sequence = useRef(0)
  const currentRequest = useRef<AbortController | null>(null)
  const last = result?.predictions.at(-1)
  const selected = scenarios.find(s => s.id === selectedScenario)
  const source = result?.provenance.sources[0]

  const changeDraft = (next: Draft) => {
    sequence.current += 1
    currentRequest.current?.abort()
    setResult(null)
    setStatus('Загрузка нового среза…')
    setDraft(next)
  }
  const calculate = () => { sequence.current += 1; currentRequest.current?.abort(); setResult(null); setRetry(value => value + 1) }
  const reset = () => { changeDraft({ ...initial }); setRetry(value => value + 1) }
  useEffect(() => {
    const id = ++sequence.current
    const controller = new AbortController()
    currentRequest.current = controller
    Promise.resolve().then(async () => {
      if (controller.signal.aborted) return
      setLoading(true)
      setStatus('Загрузка прогноза…')
      setResult(null)
      try {
        const base = await getApiBase(controller.signal)
        const registry = requestJson(base + '/scenarios', value => scenarioSchema.parse(value), controller.signal)
          .then(rows => { if (id === sequence.current) { setScenarios(rows); setScenarioError('') } })
          .catch(() => { if (id === sequence.current) setScenarioError('Реестр действий недоступен. Повторите запрос.') })
        const query = new URLSearchParams({ region: draft.region, direction: draft.direction, mode: 'sea', end_year: String(draft.end_year) })
        try {
          const response = await requestJson(base + '/forecasts?' + query, value => forecastSchema.parse(value), controller.signal)
          if (response.direction !== draft.direction || response.region !== draft.region || response.predictions.at(-1)?.period !== String(draft.end_year)) throw new Error('API вернул другой срез прогноза.')
          if (id === sequence.current) { setResult(response); setStatus('Проверенный артефакт загружен') }
        } finally { await registry }
      } catch (error) {
        if (id === sequence.current && !controller.signal.aborted) setStatus(error instanceof Error ? error.message : 'Ошибка загрузки прогноза')
      } finally {
        if (id === sequence.current) setLoading(false)
      }
    })
    return () => controller.abort()
  }, [draft, retry])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setNavOpen(false); setProfileOpen(false); setAssistantOpen(false) } }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [])
  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    document.body.style.background = theme === 'dark' ? '#0D1020' : '#F4F5FA'
  }, [theme])
  const downloadReport = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([createForecastReport(result)], { type: 'text/csv;charset=utf-8;' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'FloCast_' + result.direction + '_' + result.predictions.at(-1)?.period + '.csv'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className={`app-shell ${theme}-theme`}>
      <a className="skip-link" href="#overview">К основному содержанию</a>

      <aside className={`sidebar ${navOpen ? 'is-open' : ''}`} aria-label="Основная навигация">
        <div className="sidebar-brand">
          <a className="brand-link" href="#overview" onClick={() => { setActiveNav('overview'); setNavOpen(false) }} aria-label="Not Found — к обзору">
            <span className="brand-mark">nf</span>
            <span className="wordmark">Not Found</span>
          </a>
          <button className="icon-button sidebar-close" type="button" onClick={() => setNavOpen(false)} aria-label="Закрыть навигацию">
            <X size={20} />
          </button>
        </div>

        <nav className="main-nav">
          {navItems.map((item) => {
            const Icon = item.icon
            return (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={activeNav === item.id ? 'active' : ''}
                aria-current={activeNav === item.id ? 'page' : undefined}
                onClick={() => { setActiveNav(item.id); setNavOpen(false) }}
              >
                <Icon size={18} strokeWidth={1.8} />
                <span>{item.label}</span>
              </a>
            )
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-location">
            <MapPin size={17} />
            <span>Калининградская область<br />↔ основная часть РФ</span>
          </div>
          <small>Горизонт прогноза</small>
          <strong>2024–2030</strong>
          <div className="sidebar-edition">
            <span />Аналитический центр</div>
        </div>
      </aside>

      {navOpen && <button className="nav-backdrop" type="button" onClick={() => setNavOpen(false)} aria-label="Закрыть навигацию" />}

      <div className="main-shell">
        <main className="dashboard" id="overview">
          <header className="hero-header">
            <img className="hero-photo" src="./truck-hero.webp" alt="" />
            <div className="hero-shade" aria-hidden="true" />
            <div className={`topbar ${profileOpen ? 'profile-open' : ''}`}>
              <button className="icon-button mobile-menu" type="button" onClick={() => setNavOpen(true)} aria-label="Открыть навигацию">
                <Menu size={21} />
              </button>
              <div className="page-heading">
                <div className="heading-kicker">
                  <span />Транспортная аналитика</div>
                <h1>Прогноз грузоперевозок</h1>
                <p>Калининградская область ↔ основная часть РФ <ArrowRight size={13} /> 2024–2030</p>
              </div>

              <div className="topbar-actions">
                <span className="demo-badge">
                  <span className="pulse-dot" />Годовой ML-прогноз</span>
                <button className="icon-button assistant-toggle" type="button" onClick={() => setAssistantOpen(true)} aria-label="Открыть AI-помощника">
                  <Sparkles size={19} />
                </button>
                <button
                  className="icon-button theme-toggle"
                  type="button"
                  onClick={() => setTheme((current) => current === 'light' ? 'dark' : 'light')}
                  aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}
                  title={theme === 'light' ? 'Тёмная тема' : 'Светлая тема'}
                >
                  {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
                </button>
                <button className="export-button" type="button" onClick={downloadReport} disabled={!result || loading}>
                  <Download size={17} />
                  <span>Экспорт отчёта</span>
                </button>
                <div className="profile-wrap">
                  <button
                    className="icon-button profile-button"
                    type="button"
                    onClick={() => setProfileOpen((current) => !current)}
                    aria-label="Открыть профиль"
                    aria-expanded={profileOpen}
                    aria-controls="profile-popover"
                  >
                    <User size={18} />
                  </button>
                  {profileOpen && (
                    <div className="profile-popover" id="profile-popover" role="dialog" aria-label="Профиль команды">
                      <strong>Команда Not Found</strong>
                      <span>Аналитический контур</span>
                      <small>Локальная демо-версия. Авторизация и синхронизация не требуются.</small>
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="hero-caption">
              <span>Сценарное моделирование транспортных потоков</span>
              <span className="hero-caption-status">
                <CheckCircle2 size={14} />{loading ? 'Загрузка…' : result ? 'Прогноз загружен' : 'Нет прогноза'}</span>
            </div>
          </header>

          <p className="demo-disclaimer" role="note">Экспериментальная годовая линия Усть-Луга — Балтийск. История 2015–2023. Прогноз в тоннах за год. Точность до 2030 года не подтверждена.</p>
          <section className="filter-section" id="forecast" aria-label="Параметры прогноза">
            <div className="quick-filters">
              <div className="filter-display">
                <CalendarDays size={19} />
                <div>
                  <span>Горизонт</span>
                  <select aria-label="Конечный год" value={draft.end_year} onChange={event => changeDraft({ ...draft, end_year: Number(event.target.value) })}>{[2024, 2025, 2026, 2027, 2028, 2029, 2030].map(year => <option key={year}>{year}</option>)}</select>
                </div>
              </div>

              <label className="filter-control direction-control">
                <ArrowLeftRight size={19} />
                <div className="filter-control-content">
                  <span>Направление</span>
                  <select value={draft.direction} onChange={(event) => changeDraft({ ...draft, direction: event.target.value as Direction })} aria-label="Направление">
                    {DIRECTIONS.map((direction) => <option key={direction} value={direction}>{DIRECTION_LABELS[direction]}</option>)}
                  </select>
                </div>
                <ChevronDown size={16} />
              </label>

              <div className="filter-display">
                <Truck size={19} />
                <div>
                  <span>Вид транспорта</span>
                  <strong>Море · годовой ряд</strong>
                </div>
              </div>

              <button className="calculate-button" type="button" onClick={calculate}>
                Получить прогноз <ArrowRight size={18} />
              </button>
              <button className="reset-button" type="button" onClick={reset} aria-label="Сбросить параметры" title="Сбросить параметры">
                <RotateCcw size={18} />
              </button>
            </div>

            <button className="advanced-toggle" type="button" onClick={() => setParamsOpen((current) => !current)} aria-expanded={paramsOpen} aria-controls="advanced-panel">
              <SlidersHorizontal size={15} />
              Дополнительные условия и сценарий
              <ChevronDown size={15} className={paramsOpen ? 'rotate' : ''} />
            </button>

            <div className={'advanced-panel ' + (paramsOpen ? 'is-open' : '')} id="advanced-panel">
              <label className="filter-control">
                <span>Регион</span>
                <select aria-label="Регион" value={draft.region} onChange={event => changeDraft({ ...draft, region: event.target.value })}>
                  <option value="kaliningrad">Калининградская область</option>
                  <option value="primorye">Приморский край · пока недоступен</option>
                  <option value="kamchatka">Камчатский край · пока недоступен</option>
                </select>
              </label>
              <p>Для годовой линии доступны два направления. Численный эффект действий запрашивается отдельно.</p>
            </div>
            <div className="filter-status" role="status" aria-live="polite" aria-busy={loading}>
              <span className="status-dot" />
              <span>{status}</span>
              {!loading && !result && <button className="button-secondary" type="button" onClick={calculate}>Повторить запрос</button>}
            </div>
          </section>

          <section className="kpi-grid" aria-label="Ключевые показатели прогноза">
            <article className="kpi-card kpi-primary">
              <div className="kpi-top">
                <span>
                  <Box size={18} />Прогноз на {last?.period ?? draft.end_year}</span>
              </div>
              <div className="kpi-main">
                <div className="kpi-value" data-testid="forecast-value">{formatNumber(last?.value)}<small>т/год</small>
                </div>
              </div>
              <div className="kpi-bottom">Перевезённая масса линии</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top">
                <span>
                  <Clock3 size={18} />История модели</span>
              </div>
              <div className="kpi-main">
                <div className="kpi-value">{result ? result.observations : '—'}<small>годовых наблюдений</small>
                </div>
              </div>
              <div className="kpi-bottom">{result ? result.training_period.start + '–' + result.training_period.end : 'Модель недоступна'}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top">
                <span>
                  <MapPin size={18} />Метод</span>
              </div>
              <div className="kpi-main">
                <div className="kpi-value">{result?.selected_model ?? '—'}</div>
              </div>
              <div className="kpi-bottom">Выбор на 2020–2021</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top">
                <span>
                  <Warehouse size={18} />MAE проверки</span>
              </div>
              <div className="kpi-main">
                <div className="kpi-value">{formatNumber(result?.metrics.selected.holdout.MAE)}<small>т</small>
                </div>
              </div>
              <div className="kpi-bottom">2022–2023 · горизонт один год</div>
            </article>
          </section>
          <section className="charts-grid" id="analytics" aria-label="Графики ML-прогноза">
            <article className="chart-card">
              <div className="chart-heading">
                <BarChart3 size={17} />
                <div>
                  <h2>Перевезённая масса</h2>
                  <p>Прогноз · т/год</p>
                </div>
              </div>
              <VolumeChart result={result} />
            </article>
            <article className="chart-card">
              <div className="chart-heading">
                <TrendingUp size={17} />
                <div>
                  <h2>Динамика и неопределённость</h2>
                  <p>Условный интервал 95%</p>
                </div>
              </div>
              <DynamicsChart result={result} />
            </article>
            <article className="chart-card">
              <div className="chart-heading">
                <Layers3 size={17} />
                <div>
                  <h2>Качество и baseline</h2>
                  <p>MAE на 2022–2023, т</p>
                </div>
              </div>
              <HypothesisChart result={result} />
            </article>
          </section>
          {result && <section className="info-card forecast-table">
            <h2>Значения прогноза</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Год</th>
                    <th>Прогноз, т</th>
                    <th>Нижняя 95%, т</th>
                    <th>Верхняя 95%, т</th>
                  </tr>
                </thead>
                <tbody>{result.predictions.map(point => <tr key={point.period}>
                  <td>{point.period}</td>
                  <td>{formatNumber(point.value)}</td>
                  <td>{formatNumber(point.interval['95'].lower)}</td>
                  <td>{formatNumber(point.interval['95'].upper)}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </section>}
          <section className="scenario-layout" id="scenarios" aria-label="Сценарные действия">
            <article className="hypotheses-panel">
              <div className="panel-heading">
                <Target size={18} />
                <div>
                  <h2>Транспортные действия</h2>
                  <p>Единый реестр backend</p>
                </div>
              </div>
              {scenarioError && <p role="alert">{scenarioError}</p>}
              <div className="hypothesis-cards">{scenarios.filter(s => s.label).map(scenario => <article className="hypothesis-card" key={scenario.id}>
                <div className="hypothesis-title">
                  <span>{scenario.label}</span>
                  <h3>{scenario.name}</h3>
                </div>
                <p>{scenario.mechanism}</p>
                <p>{scenario.limitation}</p>
                <button type="button" className="button-secondary" aria-pressed={selectedScenario === scenario.id} onClick={() => setSelectedScenario(scenario.id)}>Выбрать {scenario.label}</button>
              </article>)}</div>
            </article>
            <article className="insight-panel">
              <div className="panel-heading">
                <Lightbulb size={18} />
                <div>
                  <h2>{selected?.name ?? 'Расчёт действия'}</h2>
                  <p>Чувствительность по допущениям</p>
                </div>
              </div>
              <p>{selected?.limitation ?? 'Загрузите реестр действий.'}</p>
              <p>Потребность, доступную мощность и прирост задаёт пользователь для одного периода.</p>
              <a className="button-primary" href="/capacity">Открыть калькулятор мощности</a>
              <p>Отдельное исследование сокращения портовой части оборота сохранено в материалах проекта. Его эффект не приписывается дополнительному парому.</p>
              <button className="insight-action" type="button" onClick={() => setAssistantOpen(true)}>Обсудить с ассистентом <ArrowRight size={15} />
              </button>
            </article>
          </section>
          <section className="info-grid" id="data" aria-label="Происхождение и ограничения">
            <article className="info-card">
              <div className="panel-heading">
                <Database size={18} />
                <div>
                  <h2>Источник и версии</h2>
                </div>
              </div>
              {result ? <>
                <p>{source?.organization}</p>{source && <p>
                  <a href={source.url} target="_blank" rel="noreferrer">Открыть источник</a>
                </p>}<p>Модель <code>{result.model_version}</code>
                </p>
                <p>Данные <code>{result.data_version}</code>
                </p>
                <p>{DIRECTION_LABELS[result.direction]}</p>
                <p>{result.geography.scope}</p>
              </> : <p>Источник появится после успешной проверки артефакта.</p>}
            </article>
            <article className="info-card questions-card">
              <div className="panel-heading">
                <CircleHelp size={18} />
                <div>
                  <h2>Границы проверки</h2>
                </div>
              </div>
              <p>Годовая линия Усть-Луга — Балтийск. Месячный каботаж и другие регионы не входят в этот результат.</p>
              <p>Сохранённая модель загружается без обучения. Методологическая приёмка ожидается. Первичные наблюдения выгрузки повторно не сверялись.</p>
            </article>
            <article className="info-card">
              <div className="panel-heading">
                <Info size={18} />
                <div>
                  <h2>Ограничения прогноза</h2>
                </div>
              </div>
              <ul>{result?.warnings.map(warning => <li key={warning}>{warning}</li>) ?? <li>Прогноз отсутствует. Значения при ошибке не подставляются.</li>}</ul>
            </article>
          </section>
          <section className="about-card" id="about" aria-labelledby="about-title">
            <div className="about-icon">
              <Info size={20} />
            </div>
            <div>
              <h2 id="about-title">О проекте</h2>
              <p>FloCast — исследовательский прототип анализа перевозок. Этот экран подключён к существующей модели годовой линии. Параметры сценарной мощности задаются в отдельном калькуляторе.</p>
              <p>
                <a href="/capacity">Калькулятор мощности по вводу пользователя</a>
              </p>
            </div>
            <span className="about-tag">Not Found / 2030</span>
          </section>
        </main>

        <footer className="site-footer">
          <span>Not Found · Инструмент прогноза грузовых перевозок</span>
          <span>Экспериментальный прогноз · 2024–2030</span>
        </footer>
      </div>

      {assistantOpen && <button className="assistant-backdrop" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-помощника" />}

      <aside className={`assistant-panel ${assistantOpen ? 'is-open' : ''}`} aria-label="AI-помощник">
        <header className="assistant-header">
          <div className="assistant-title-icon">
            <Sparkles size={20} />
          </div>
          <div>
            <h2>AI-помощник</h2>
            <p>
              <span />Yandex AI Studio</p>
          </div>
          <div className="assistant-header-actions">
            <button className="assistant-icon-button assistant-close" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-помощника">
              <X size={19} />
            </button>
          </div>
        </header>

        <AiAssistant open={assistantOpen} theme={theme} onOpen={() => setAssistantOpen(true)} />
      </aside>
    </div>
  )
}
