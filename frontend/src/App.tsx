import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowDownRight,
  ArrowLeftRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Box,
  CalendarDays,
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
  Send,
  SlidersHorizontal,
  Sparkles,
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
  DIRECTION_LABELS,
  selectDemoScenario,
  formatNumber,
  signed,
  type ForecastConfig,
  type Scenario,
} from './dashboard-data'
import { createDemoReport } from './report'
import './App.css'

type Theme = 'dark' | 'light'
type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string }

const copyConfig = (config: ForecastConfig): ForecastConfig => ({ ...config })

const navItems = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'forecast', label: 'Демо-пример', icon: BarChart3 },
  { id: 'scenarios', label: 'Сценарии', icon: Layers3 },
  { id: 'analytics', label: 'Аналитика', icon: TrendingUp },
  { id: 'data', label: 'Данные', icon: Database },
  { id: 'about', label: 'О проекте', icon: Info },
]

function Delta({ value, suffix = '%', inverse = false }: { value: number; suffix?: string; inverse?: boolean }) {
  const positive = value >= 0
  const favorable = inverse ? !positive : positive
  return <span className={`delta ${favorable ? 'delta-good' : 'delta-warn'}`}>{signed(value, suffix)}</span>
}

function escapeHtml(value: string): string {
  const div = document.createElement('div')
  div.textContent = value
  return div.innerHTML
}

export default function App() {
  const [draft, setDraft] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [applied, setApplied] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [paramsOpen, setParamsOpen] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('overview')
  const [theme, setTheme] = useState<Theme>('light')
  const [notice, setNotice] = useState('Исходный синтетический пример')

  const [apiBase, setApiBase] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [questionInput, setQuestionInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)

  const result = useMemo(() => selectDemoScenario(applied), [applied])
  const dirty = JSON.stringify(draft) !== JSON.stringify(applied)
  const scenarioTitle = SCENARIOS.find((scenario) => scenario.id === applied.scenario)?.title ?? 'Базовый сценарий'
  const modeLabel = 'море · синтетический пример'

  useEffect(() => {
    const closeOverlays = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setNavOpen(false)
        setProfileOpen(false)
        setAssistantOpen(false)
      }
    }
    window.addEventListener('keydown', closeOverlays)
    return () => window.removeEventListener('keydown', closeOverlays)
  }, [])

  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    document.documentElement.style.background = theme === 'dark' ? '#0D1020' : '#F4F5FA'
    document.body.style.background = theme === 'dark' ? '#0D1020' : '#F4F5FA'
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0D1020' : '#F4F5FA')
  }, [theme])

  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await fetch(new URL('/config.json', window.location.origin), { cache: 'no-store' })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const config = await response.json()
        const base = config.apiBase
        if (typeof base !== 'string' ||
            !(base.startsWith('/') && !base.startsWith('//') || /^https?:\/\//i.test(base))) {
          throw new Error('Некорректный адрес API')
        }
        const url = new URL(base, window.location.origin)
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Некорректный адрес API')
        setApiBase(url.href.replace(/\/+$/, ''))
        setApiError(null)
      } catch {
        setApiBase(null)
        setApiError('Не удалось загрузить адрес API. Проверьте config.json и доступность сервера.')
      }
    }
    loadConfig()
  }, [])

  const callAssistant = useCallback(async (endpoint: 'summary' | 'ask', body?: { question: string }) => {
    if (!apiBase) {
      throw new Error(apiError || 'Адрес API не настроен')
    }
    const url = `${apiBase}/llm/${endpoint}`
    const response = await fetch(url, {
      method: endpoint === 'summary' ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    let data: unknown
    try {
      data = await response.json()
    } catch {
      throw new Error('Сервер вернул некорректный JSON-ответ')
    }
    if (!response.ok) {
      const error = (data as { error?: { message?: string; code?: string } } | undefined)?.error
      const message = error?.message || `Ошибка API (${response.status})`
      throw new Error(message)
    }
    const answer = (data as { answer?: string } | undefined)?.answer
    if (typeof answer !== 'string') {
      throw new Error('Сервер вернул ответ без поля answer')
    }
    return answer
  }, [apiBase, apiError])

  const fetchSummary = useCallback(async () => {
    if (!assistantOpen || isLoading) return
    setIsLoading(true)
    setChatError(null)
    try {
      const answer = await callAssistant('summary')
      setChatHistory((prev) => [
        ...prev,
        { role: 'system', content: 'Автоматическая сводка по данным дашборда' },
        { role: 'assistant', content: answer },
      ])
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Не удалось получить сводку')
    } finally {
      setIsLoading(false)
    }
  }, [assistantOpen, isLoading, callAssistant])

  useEffect(() => {
    if (assistantOpen && chatHistory.length === 0 && !isLoading && !chatError) {
      fetchSummary()
    }
  }, [assistantOpen, chatHistory.length, isLoading, chatError, fetchSummary])

  async function handleAsk(event: React.FormEvent) {
    event.preventDefault()
    const question = questionInput.trim()
    if (!question || isLoading) return
    setQuestionInput('')
    setChatError(null)
    setChatHistory((prev) => [...prev, { role: 'user', content: question }])
    setIsLoading(true)
    try {
      const answer = await callAssistant('ask', { question })
      setChatHistory((prev) => [...prev, { role: 'assistant', content: answer }])
    } catch (error) {
      setChatError(error instanceof Error ? error.message : 'Не удалось получить ответ')
      setQuestionInput(question)
    } finally {
      setIsLoading(false)
    }
  }

  const calculate = () => {
    setApplied(copyConfig(draft))
    setNotice('Синтетический пример выбран')
    setParamsOpen(false)
  }

  const reset = () => {
    setDraft(copyConfig(DEFAULT_CONFIG))
    setApplied(copyConfig(DEFAULT_CONFIG))
    setNotice('Исходные параметры восстановлены')
  }

  const exportReport = () => {
    const csv = createDemoReport(applied)
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `Синтетический_пример_${DIRECTION_LABELS[applied.direction]}_2030.csv`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice('Отчёт CSV выгружен')
  }

  const insight = 'Синтетические примеры для проверки интерфейса. Эффекты действий и два основных сценария командой не утверждены.'

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
          <strong>2025–2030</strong>
          <div className="sidebar-edition"><span />Аналитический центр</div>
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
                <div className="heading-kicker"><span />Транспортная аналитика</div>
                <h1>Демо грузоперевозок</h1>
                <p>Калининградская область ↔ основная часть РФ <ArrowRight size={13} /> 2025–2030</p>
              </div>

              <div className="topbar-actions">
                <span className="demo-badge"><span className="pulse-dot" />Синтетические данные</span>
                <button className="icon-button assistant-toggle" type="button" onClick={() => setAssistantOpen(true)} aria-label="Открыть AI-ассистента">
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
                <button className="export-button" type="button" onClick={exportReport}>
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
              <span className="hero-caption-status"><CheckCircle2 size={14} />Расчёт готов</span>
            </div>
          </header>

          <p className="demo-disclaimer" role="note">Все числа — synthetic, включая 2025 год. Это вымышленные примеры, не наблюдения и не прогноз. Источника фактических данных нет. Единицы: тыс. т/сутки, ч, %.</p>
          <section className="filter-section" id="forecast" aria-label="Параметры прогноза">
            <div className="quick-filters">
              <div className="filter-display">
                <CalendarDays size={19} />
                <div><span>Горизонт</span><strong>2030 год</strong></div>
              </div>

              <label className="filter-control direction-control">
                <ArrowLeftRight size={19} />
                <div className="filter-control-content">
                  <span>Направление</span>
                  <select value={draft.direction} onChange={(event) => setDraft((current) => ({ ...current, direction: event.target.value as ForecastConfig['direction'] }))} aria-label="Направление">
                    {DIRECTIONS.map((direction) => <option key={direction} value={direction}>{DIRECTION_LABELS[direction]}</option>)}
                  </select>
                </div>
                <ChevronDown size={16} />
              </label>

              <div className="filter-display"><Truck size={19} /><div><span>Вид транспорта</span><strong>Море · синтетика</strong></div></div>

              <button className="calculate-button" type="button" onClick={calculate}>
                Показать пример <ArrowRight size={18} />
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

            <div className={`advanced-panel ${paramsOpen ? 'is-open' : ''}`} id="advanced-panel">
              <fieldset>
                <legend>Сценарий развития</legend>
                <div className="scenario-options">
                  {SCENARIOS.map((scenario) => (
                    <label key={scenario.id} className={draft.scenario === scenario.id ? 'selected' : ''}>
                      <input type="radio" name="scenario" value={scenario.id} checked={draft.scenario === scenario.id} onChange={() => setDraft((current) => ({ ...current, scenario: scenario.id as Scenario }))} />
                      <span className="scenario-radio" />
                      <span><strong>{scenario.short}</strong><small>{scenario.description}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="advanced-actions">
                <button className="button-primary" type="button" onClick={calculate}>Применить <ArrowRight size={15} /></button>
                <button className="button-secondary" type="button" onClick={reset}><RotateCcw size={14} />Сбросить</button>
              </div>
            </div>

            <div className="filter-status" role="status" aria-live="polite">
              <span className={`status-dot ${dirty ? 'is-dirty' : ''}`} />
              <span>{dirty ? 'Параметры изменены — нажмите «Показать пример»' : notice}</span>
              <span className="current-scenario">{scenarioTitle}</span>
            </div>
          </section>

          <section className="kpi-grid" aria-label="Ключевые показатели прогноза">
            <article className="kpi-card kpi-primary">
              <div className="kpi-top"><span><Box size={18} />Объём груза к 2030</span><span className="kpi-index">01</span></div>
              <div className="kpi-main">
                <div className="kpi-value">{formatNumber(result.volume)}<small>тыс. т/сутки</small></div>
                <Delta value={result.volumeDelta} />
              </div>
              <div className="kpi-bottom"><ArrowUpRight size={13} />к 2025 году</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><Clock3 size={18} />Время доставки</span><span className="kpi-index">02</span></div>
              <div className="kpi-main">
                <div className="kpi-value">{formatNumber(result.time)}<small>{result.timeUnit}</small></div>
                <Delta value={result.timeDelta} inverse />
              </div>
              <div className="kpi-bottom"><ArrowDownRight size={13} />к 2025 году · {modeLabel}</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><MapPin size={18} />Доступность городов</span><span className="kpi-index">03</span></div>
              <div className="kpi-main">
                <div className="kpi-value">+{result.access}<small>%</small></div>
                <Delta value={result.access} />
              </div>
              <div className="kpi-bottom"><ArrowUpRight size={13} />к 2025 году</div>
            </article>
            <article className="kpi-card">
              <div className="kpi-top"><span><Warehouse size={18} />Загрузка терминала</span><span className="kpi-index">04</span></div>
              <div className="kpi-main">
                <div className="kpi-value">{result.terminal}<small>%</small></div>
                <Delta value={result.terminalDelta} suffix=" п.п." />
              </div>
              <div className="kpi-bottom"><ArrowUpRight size={13} />{DIRECTION_LABELS[applied.direction]} · к 2025 году</div>
            </article>
          </section>

          <section className="charts-grid" id="analytics" aria-label="Аналитические графики">
            <article className="chart-card">
              <div className="chart-heading">
                <BarChart3 size={17} />
                <div><h2>Объём груза к 2030</h2><p>{scenarioTitle}</p></div>
                <span className="chart-period">2025 / 2030</span>
              </div>
              <VolumeChart data={result.stacked} light={theme === 'light'} />
            </article>
            <article className="chart-card">
              <div className="chart-heading">
                <TrendingUp size={17} />
                <div><h2>Динамика по годам</h2><p>Три сценария развития</p></div>
                <span className="chart-period">6 лет</span>
              </div>
              <DynamicsChart data={result.dynamics} light={theme === 'light'} />
            </article>
            <article className="chart-card">
              <div className="chart-heading">
                <Layers3 size={17} />
                <div><h2>Объём и время доставки</h2><p>Сопоставление гипотез</p></div>
                <span className="chart-period">2030</span>
              </div>
              <HypothesisChart data={result.hypotheses} timeUnit={result.timeUnit} light={theme === 'light'} />
            </article>
          </section>

          <section className="scenario-layout" id="scenarios" aria-label="Сравнение сценариев">
            <article className="hypotheses-panel">
              <div className="panel-heading">
                <Target size={18} />
                <div><h2>Сравнение гипотез действий</h2><p>Изменение относительно базового сценария</p></div>
              </div>
              <div className="hypothesis-cards">
                {[result.hypotheses[1], result.hypotheses[2]].map((hypothesis, index) => {
                  const base = result.hypotheses[0]
                  const volumeChange = Math.round((hypothesis.volume / base.volume - 1) * 100)
                  const timeChange = Math.round((hypothesis.time / base.time - 1) * 100)
                  return (
                    <article className="hypothesis-card" key={hypothesis.name}>
                      <div className="hypothesis-title">
                        <span>{index === 0 ? 'А' : 'Б'}</span>
                        <h3>{index === 0 ? 'Больше мощности' : 'Новый маршрут'}</h3>
                      </div>
                      <div className="hypothesis-value">
                        +{volumeChange}%<small>к объёму</small>
                        <Delta value={timeChange} inverse />
                      </div>
                      <div className="hypothesis-meta">
                        <span><Box size={13} />{formatNumber(hypothesis.volume)} тыс. т/сутки</span>
                        <span><Clock3 size={13} />{formatNumber(hypothesis.time)} {hypothesis.unit}</span>
                      </div>
                      <p><Route size={14} />{index === 0 ? 'Рост частоты рейсов и пропускной способности без новой связи.' : 'Новая связь между городами с наибольшим потенциалом роста.'}</p>
                    </article>
                  )
                })}
              </div>
            </article>

            <article className="insight-panel">
              <div className="panel-heading"><Lightbulb size={18} /><div><h2>Что меняет выбор</h2><p>Ключевой эффект сценария</p></div></div>
              <p>{insight}</p>
              <div className="insight-metrics">
                <span><strong>{formatNumber(result.hypotheses[2].volume)}</strong> тыс. т/сутки</span>
                <span><strong>{formatNumber(result.hypotheses[2].time)}</strong> {result.timeUnit}</span>
              </div>
              <button type="button" className="insight-action" onClick={() => setAssistantOpen(true)}>
                Обсудить с ассистентом <ArrowRight size={15} />
              </button>
            </article>
          </section>

          <section className="info-grid" id="data" aria-label="Выводы и ограничения данных">
            <article className="info-card">
              <div className="panel-heading"><CheckCircle2 size={18} /><div><h2>Выводы</h2><p>По текущему расчёту</p></div></div>
              <ul>
                <li>Объём к 2030 году — <strong>{formatNumber(result.volume)} тыс. т/сутки</strong>, изменение {signed(result.volumeDelta)}.</li>
                <li>Сценарий «Б. Новый маршрут» лидирует по объёму среди трёх гипотез.</li>
                <li>Терминал «{DIRECTION_LABELS[applied.direction]}» загружен на <strong>{result.terminal}%</strong>; запас мощности нужно проверять отдельно.</li>
              </ul>
            </article>
            <article className="info-card questions-card">
              <div className="panel-heading"><CircleHelp size={18} /><div><h2>Вопросы для проверки</h2><p>Следующий этап анализа</p></div></div>
              <ul>
                <li>Как сезонность меняет интенсивность потока?</li>
                <li>Какова подтверждённая предельная мощность терминала?</li>
                <li>Как тарифы влияют на выбор маршрута и вида транспорта?</li>
              </ul>
              <span>Приоритет: пропускная способность</span>
            </article>
            <article className="info-card">
              <div className="panel-heading"><Database size={18} /><div><h2>Ограничения данных</h2><p>Границы интерпретации</p></div></div>
              <p><strong>Все числа иллюстративные.</strong> Модель не использует внешние источники, фактические перевозки, тарифы, ДТП, погоду и дорожные работы.</p>
              <p>Проверенные данные и обученная модель не подключены. Время, доступность и эффекты действий вымышлены.</p>
            </article>
          </section>

          <section className="about-card" id="about" aria-labelledby="about-title">
            <div className="about-icon"><Info size={20} /></div>
            <div>
              <h2 id="about-title">О проекте</h2>
              <p>«Инструмент прогноза грузовых перевозок» — демонстрационный аналитический продукт команды Not Found для исследования развития транспортной системы Калининградской агломерации. Он помогает сопоставлять направления, виды транспорта и сценарии развития на горизонте 2025–2030 годов, быстро оценивать ключевые показатели и формулировать вопросы для последующей проверки на реальных данных.</p>
              <p><a href="/capacity">Калькулятор мощности по вводу пользователя</a></p>
            </div>
            <span className="about-tag">Not Found / 2030</span>
          </section>
        </main>

        <footer className="site-footer">
          <span>Not Found · Инструмент прогноза грузовых перевозок</span>
          <span>Демонстрационные данные · 2025–2030</span>
        </footer>
      </div>

      {assistantOpen && <button className="assistant-backdrop" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента" />}

      <aside className={`assistant-panel ${assistantOpen ? 'is-open' : ''}`} aria-label="AI-ассистент по данным дашборда">
        <header className="assistant-header">
          <div className="assistant-title-icon"><Sparkles size={20} /></div>
          <div>
            <h2>AI-ассистент</h2>
            <p><span />Подключён к основному backend</p>
          </div>
          <div className="assistant-header-actions">
            <button
              className="assistant-text-button"
              type="button"
              onClick={() => { setChatHistory([]); setChatError(null) }}
              disabled={isLoading || !apiBase}
              aria-label="Сделать новую сводку"
            >
              Сводка
            </button>
            <button className="assistant-icon-button assistant-close" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента"><X size={19} /></button>
          </div>
        </header>

        <div className="assistant-context">
          <div><MapPin size={15} /><span>{DIRECTION_LABELS[applied.direction]}</span><span className="context-separator">·</span><span>2030</span></div>
          <p>{modeLabel}</p>
        </div>

        <div className="chat-messages" aria-live="polite" aria-label="История диалога">
          {chatHistory.length === 0 && !isLoading && !chatError && (
            <div className="chat-welcome">
              <Sparkles size={28} />
              <p>Для анализа данных нажмите «Сводка»</p>
              <span>Ассистент ответит по контексту из JSON-файлов backend</span>
            </div>
          )}
          {chatHistory.map((message, index) => (
            <div
              key={index}
              className={`chat-message ${message.role}`}
              dangerouslySetInnerHTML={{ __html: escapeHtml(message.content) }}
            />
          ))}
          {isLoading && (
            <div className="chat-loading" aria-label="Ассистент печатает">
              <span className="chat-loading-dot" />
              <span className="chat-loading-dot" />
              <span className="chat-loading-dot" />
            </div>
          )}
        </div>

        {chatError && <div className="chat-error" role="alert">{chatError}</div>}

        <form className="chat-input-area" onSubmit={handleAsk}>
          <textarea
            className="chat-input"
            value={questionInput}
            onChange={(event) => setQuestionInput(event.target.value)}
            placeholder={apiBase ? 'Введите вопрос по данным дашборда…' : 'API не настроен'}
            disabled={!apiBase || isLoading}
            rows={3}
            aria-label="Вопрос ассистенту"
          />
          <button
            className="chat-submit"
            type="submit"
            disabled={!apiBase || isLoading || questionInput.trim().length === 0}
            aria-label="Отправить вопрос"
          >
            <Send size={14} /> Отправить
          </button>
          <span className="chat-hint">Ответ формируется по переданному контексту. Это не наблюдение, прогноз или сценарный расчёт.</span>
        </form>
      </aside>
    </div>
  )
}
