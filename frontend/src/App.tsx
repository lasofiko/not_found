import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight, ArrowUp, ArrowUpRight, BarChart3, Box, Calculator, CheckCircle2, Clock3,
  Database, Download, Info, Layers3, LayoutDashboard, MapPin,
  Menu, Moon, Paperclip, RotateCcw, Route, Sparkles, Sun, Truck, X,
} from 'lucide-react'
import {
  DynamicsChart,
  ForecastDynamicsChart,
  ForecastVolumeChart,
  HypothesisChart,
  ModelQualityChart,
  VolumeChart,
} from './ChartPanels'
import {
  DEFAULT_CONFIG,
  DIRECTION_LABELS,
  SCENARIOS,
  selectDemoScenario,
  formatNumber,
  signed,
  type Direction,
  type ForecastConfig,
  type Scenario,
} from './dashboard-data'
import { forecastSchema, getApiBase, requestJson, scenarioSchema } from './api'
import type { Forecast, ScenarioDefinition } from './api'
import { appendAssistantReply, assistantSelection, createAssistantRequests, isAssistantSelectionSupported, requestAssistant } from './assistant'
import type { AssistantMessage } from './assistant'
import { createDemoReport, createForecastReport } from './report'
import { TransportGallery } from './TransportGallery'
import { FilterSelect } from './FilterSelect'
import { GeographyFilters } from './GeographyFilters'
import { CapacityCalculator } from './CapacityCalculator'
import { DEFAULT_GEOGRAPHY, CORRIDORS, isDemoAvailable, mapLinks, routeSelection, selectionReport, type Geography } from './geography'
import './App.css'

const copyConfig = (config: ForecastConfig): ForecastConfig => ({ ...config })
function formatTime(date: Date = new Date()): string {
  return date.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
type Theme = 'dark' | 'light'

const navItems = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'analytics', label: 'Аналитика', icon: BarChart3 },
  { id: 'transport', label: 'Транспорт', icon: Truck },
  { id: 'routes', label: 'Маршруты', icon: Route },
  { id: 'forecast', label: 'Прогноз', icon: ArrowUpRight },
  { id: 'scenarios', label: 'Сценарии', icon: Layers3 },
  { id: 'capacity', label: 'Калькулятор', icon: Calculator },
  { id: 'data', label: 'Данные', icon: Database },
]

function escapeHtml(value: string): string {
  const div = document.createElement('div')
  div.textContent = value
  return div.innerHTML
}

function parseInlineMarkdown(line: string): string {
  return line
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
}

function isTableRow(line: string): boolean {
  const trimmed = line.trim()
  return trimmed.startsWith('|') && trimmed.endsWith('|')
}

function parseTableRow(line: string): string[] {
  return line.trim().slice(1, -1).split('|').map((cell) => cell.trim())
}

function isTableSeparator(line: string): boolean {
  const cells = parseTableRow(line)
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell))
}

function buildTable(rows: string[]): string {
  if (rows.length < 2) {
    return `<p>${rows.map(parseInlineMarkdown).join('<br>')}</p>`
  }
  const headerCells = parseTableRow(rows[0])
  const hasSeparator = isTableSeparator(rows[1])
  const bodyRows = hasSeparator ? rows.slice(2) : rows.slice(1)
  const header = `<thead><tr>${headerCells.map((cell) => `<th>${parseInlineMarkdown(cell)}</th>`).join('')}</tr></thead>`
  const body = `<tbody>${bodyRows.map((row) => {
    const cells = parseTableRow(row)
    return `<tr>${cells.map((cell) => `<td>${parseInlineMarkdown(cell)}</td>`).join('')}</tr>`
  }).join('')}</tbody>`
  return `<table>${header}${body}</table>`
}

function formatAnswer(raw: string): string {
  const escaped = escapeHtml(raw)
  const lines = escaped.split('\n')
  const blocks: string[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') {
      i += 1
      continue
    }
    const headingMatch = line.match(/^(#{1,6}) (.*)$/)
    if (headingMatch) {
      const tag = `h${Math.min(6, headingMatch[1].length + 2)}`
      blocks.push(`<${tag}>${parseInlineMarkdown(headingMatch[2])}</${tag}>`)
      i += 1
      continue
    }
    if (isTableRow(line)) {
      const rows: string[] = []
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(lines[i])
        i += 1
      }
      blocks.push(buildTable(rows))
      continue
    }
    const listMatch = line.match(/^(\s*)[-*] (.*)$/)
    if (listMatch) {
      const items: string[] = []
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)[-*] (.*)$/)
        if (!m) break
        items.push(`<li>${parseInlineMarkdown(m[2])}</li>`)
        i += 1
      }
      blocks.push(`<ul>${items.join('')}</ul>`)
      continue
    }
    let paragraph = ''
    while (i < lines.length && lines[i].trim() !== '') {
      paragraph += (paragraph ? '<br>' : '') + parseInlineMarkdown(lines[i])
      i += 1
    }
    blocks.push(`<p>${paragraph}</p>`)
  }
  return blocks.join('')
}

function Delta({ value, suffix = '%', inverse = false }: { value: number; suffix?: string; inverse?: boolean }) {
  const positive = value >= 0
  const favorable = inverse ? !positive : positive
  return <span className={`delta ${favorable ? 'delta-good' : 'delta-warn'}`}>{signed(value, suffix)}</span>
}

export default function App() {
  const [draft, setDraft] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [applied, setApplied] = useState<ForecastConfig>(() => copyConfig(DEFAULT_CONFIG))
  const [draftGeo, setDraftGeo] = useState<Geography>({ ...DEFAULT_GEOGRAPHY })
  const [appliedGeo, setAppliedGeo] = useState<Geography>({ ...DEFAULT_GEOGRAPHY })
  const [navOpen, setNavOpen] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('overview')
  const [theme, setTheme] = useState<Theme>(() => {
    try { return localStorage.getItem('flocast-theme') === 'dark' ? 'dark' : 'light' }
    catch { return 'light' }
  })
  const menuButton = useRef<HTMLButtonElement>(null)
  const result = useMemo(() => selectDemoScenario(applied), [applied])
  const dirty = JSON.stringify(draft) !== JSON.stringify(applied) || JSON.stringify(draftGeo) !== JSON.stringify(appliedGeo)
  const hasDemo = isDemoAvailable(appliedGeo)
  const selectedRoute = routeSelection(appliedGeo, applied.direction)
  const selectedMap = mapLinks(appliedGeo, applied.direction)
  const corridorLabel = CORRIDORS.find(({ id }) => id === appliedGeo.corridor)!.label
  const coverageMessage = hasDemo
    ? 'Это синтетический пример Калининградского коридора, а не наблюдения или прогноз. Проверенный годовой ML-прогноз показан отдельно ниже.'
    : 'Синтетический пример для выбранной детализации недоступен. Годовой ML-прогноз ниже относится только к линии Усть-Луга — Балтийск.'
  const unavailable = <div className="coverage-empty"><MapPin size={25} /><h3>Нет данных для выбранной детализации</h3><p>{coverageMessage}</p></div>
  const scenarioTitle = SCENARIOS.find((scenario) => scenario.id === applied.scenario)?.title ?? 'Базовый сценарий'

  const [apiBase, setApiBase] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [configRetry, setConfigRetry] = useState(0)
  const [chatHistory, setChatHistory] = useState<AssistantMessage[]>([])
  const [questionInput, setQuestionInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const summaryRequestedRef = useRef(false)
  const chatMessagesRef = useRef<HTMLDivElement>(null)
  const chatRequests = useRef(createAssistantRequests())
  const assistantChoice = useMemo(() => assistantSelection(appliedGeo, applied.direction), [appliedGeo, applied.direction])
  const assistantSupported = isAssistantSelectionSupported(assistantChoice)
  const assistantKey = JSON.stringify([apiBase, assistantChoice])
  const [chatContextKey, setChatContextKey] = useState('')
  const [chatContext, setChatContext] = useState<Forecast | null>(null)
  const currentChat = chatContextKey === assistantKey
  const visibleChatHistory = currentChat ? chatHistory : []
  const visibleChatContext = currentChat ? chatContext : null
  const visibleChatError = currentChat ? chatError : null
  const chatLoading = currentChat && isLoading
  const [forecastDraft, setForecastDraft] = useState<{ direction: Direction; end_year: number }>({ direction: 'inbound', end_year: 2030 })
  const [forecastResult, setForecastResult] = useState<Forecast | null>(null)
  const [forecastStatus, setForecastStatus] = useState('Загрузка прогноза…')
  const [forecastLoading, setForecastLoading] = useState(true)
  const [forecastRetry, setForecastRetry] = useState(0)
  const [scenarioDefinitions, setScenarioDefinitions] = useState<ScenarioDefinition[]>([])
  const [scenarioRegistryError, setScenarioRegistryError] = useState('')
  const forecastSequence = useRef(0)
  const initialSection = useRef(window.location.hash.slice(1))
  const initialSectionHandled = useRef(false)
  const lastForecast = forecastResult?.predictions.at(-1)
  const forecastSource = forecastResult?.provenance.sources[0]

  useEffect(() => {
    const element = chatMessagesRef.current
    if (element) {
      element.scrollTop = element.scrollHeight
    }
  }, [chatHistory, isLoading, chatError])

  useEffect(() => {
    const closeOverlays = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setNavOpen(false)
        if (navOpen) menuButton.current?.focus()
        setAssistantOpen(false)
      }
    }
    window.addEventListener('keydown', closeOverlays)
    return () => window.removeEventListener('keydown', closeOverlays)
  }, [navOpen])

  useEffect(() => {
    try { localStorage.setItem('flocast-theme', theme) } catch { /* Storage may be disabled. */ }
    document.documentElement.style.colorScheme = theme
    document.documentElement.style.background = theme === 'dark' ? '#0D1020' : '#F4F5FA'
    document.body.style.background = theme === 'dark' ? '#0D1020' : '#F4F5FA'
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0D1020' : '#F4F5FA')
  }, [theme])

  useEffect(() => {
    let frame = 0
    const updateSection = () => {
      frame = 0
      const current = navItems.filter(({ id }) =>
        (document.getElementById(id)?.getBoundingClientRect().top ?? Infinity) <= 120,
      ).at(-1)
      setActiveNav(current?.id ?? 'overview')
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(updateSection) }
    schedule()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    window.addEventListener('hashchange', schedule)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('hashchange', schedule)
    }
  }, [])

  useEffect(() => {
    if (!navOpen) return
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Element && !event.target.closest('.sidebar')) setNavOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [navOpen])

  useEffect(() => {
    const controller = new AbortController()
    async function loadConfig() {
      try {
        const base = await getApiBase(controller.signal)
        if (controller.signal.aborted) return
        setApiBase(base)
        setApiError(null)
      } catch (error) {
        if (controller.signal.aborted) return
        setApiBase(null)
        setApiError(error instanceof Error ? error.message : 'Не удалось загрузить адрес API.')
      }
    }
    loadConfig()
    return () => controller.abort()
  }, [configRetry])

  useEffect(() => {
    if (forecastLoading || initialSectionHandled.current) return
    const id = initialSection.current
    if (window.location.hash.slice(1) !== id || !navItems.some(item => item.id === id)) return
    let cancelled = false
    let frame = 0
    void document.fonts.ready.then(() => {
      if (cancelled) return
      frame = requestAnimationFrame(() => {
        if (window.location.hash.slice(1) !== id) return
        document.getElementById(id)?.scrollIntoView({ behavior: 'instant' })
        initialSectionHandled.current = true
      })
    })
    return () => { cancelled = true; cancelAnimationFrame(frame) }
  }, [forecastLoading])

  useEffect(() => {
    const id = ++forecastSequence.current
    const controller = new AbortController()
    Promise.resolve().then(async () => {
      if (controller.signal.aborted) return
      setForecastLoading(true)
      setForecastStatus('Загрузка прогноза…')
      setForecastResult(null)
      try {
        const base = await getApiBase(controller.signal)
        const registry = requestJson(base + '/scenarios', value => scenarioSchema.parse(value), controller.signal)
          .then(rows => {
            if (id === forecastSequence.current) {
              setScenarioDefinitions(rows)
              setScenarioRegistryError('')
            }
          })
          .catch(() => {
            if (id === forecastSequence.current) setScenarioRegistryError('Реестр действий недоступен. Повторите запрос.')
          })
        const query = new URLSearchParams({
          region: 'kaliningrad',
          direction: forecastDraft.direction,
          mode: 'sea',
          end_year: String(forecastDraft.end_year),
        })
        try {
          const response = await requestJson(base + '/forecasts?' + query, value => forecastSchema.parse(value), controller.signal)
          if (response.direction !== forecastDraft.direction || response.predictions.at(-1)?.period !== String(forecastDraft.end_year)) {
            throw new Error('API вернул другой срез прогноза.')
          }
          if (id === forecastSequence.current) {
            setForecastResult(response)
            setForecastStatus('Проверенный артефакт загружен')
          }
        } finally {
          await registry
        }
      } catch (error) {
        if (id === forecastSequence.current && !controller.signal.aborted) {
          setForecastStatus(error instanceof Error ? error.message : 'Ошибка загрузки прогноза')
        }
      } finally {
        if (id === forecastSequence.current) setForecastLoading(false)
      }
    })
    return () => controller.abort()
  }, [forecastDraft, forecastRetry])

  const clearChat = useCallback(() => {
    chatRequests.current.cancel()
    summaryRequestedRef.current = false
    setChatHistory([])
    setQuestionInput('')
    setChatError(null)
    setChatContext(null)
    setIsLoading(false)
    setChatContextKey(assistantKey)
  }, [assistantKey])

  useEffect(() => {
    const requests = chatRequests.current
    let active = true
    requests.cancel()
    void Promise.resolve().then(() => { if (active) clearChat() })
    return () => { active = false; requests.cancel() }
  }, [clearChat])

  const fetchSummary = useCallback(async () => {
    if (!assistantOpen || !apiBase || !assistantSupported || !currentChat) return
    await chatRequests.current.run(signal => requestAssistant(apiBase, assistantChoice, signal), {
      start: () => {
        summaryRequestedRef.current = true
        setIsLoading(true)
        setChatError(null)
      },
      success: (response) => {
        setChatContext(response.context)
        setChatHistory((prev) => appendAssistantReply(prev, chatContext, response, formatTime()))
      },
      error: (error) => setChatError(error instanceof Error ? error.message : 'Не удалось получить сводку'),
      settled: () => setIsLoading(false),
    })
  }, [assistantOpen, apiBase, assistantSupported, currentChat, assistantChoice, chatContext])

  useEffect(() => {
    if (assistantOpen && apiBase && assistantSupported && currentChat && chatHistory.length === 0 && !summaryRequestedRef.current && !chatError) {
      void fetchSummary()
    }
  }, [assistantOpen, apiBase, assistantSupported, currentChat, chatHistory.length, chatError, fetchSummary])

  async function handleAsk(event: React.FormEvent) {
    event.preventDefault()
    const question = questionInput.trim()
    if (!question || isLoading || !apiBase || !assistantSupported || !currentChat) return
    await chatRequests.current.run(signal => requestAssistant(apiBase, assistantChoice, signal, question), {
      start: () => {
        setQuestionInput('')
        setChatError(null)
        setChatHistory((prev) => [...prev, { role: 'user', content: question, createdAt: formatTime() }])
        setIsLoading(true)
      },
      success: (response) => {
        setChatContext(response.context)
        setChatHistory((prev) => appendAssistantReply(prev, chatContext, response, formatTime(), question))
      },
      error: (error) => {
        setChatError(error instanceof Error ? error.message : 'Не удалось получить ответ')
        setQuestionInput(question)
      },
      settled: () => setIsLoading(false),
    })
  }

  const calculate = () => {
    setApplied(copyConfig(draft))
    setAppliedGeo({ ...draftGeo })
  }

  const reset = () => {
    setDraft(copyConfig(DEFAULT_CONFIG))
    setApplied(copyConfig(DEFAULT_CONFIG))
    setDraftGeo({ ...DEFAULT_GEOGRAPHY })
    setAppliedGeo({ ...DEFAULT_GEOGRAPHY })
  }

  const exportReport = () => {
    const csv = hasDemo ? createDemoReport(applied) : selectionReport(appliedGeo, applied.direction)
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `FloCast_${appliedGeo.corridor}_${applied.direction}_${hasDemo ? 'synthetic' : 'selection'}.csv`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const exportForecast = () => {
    if (!forecastResult) return
    const url = URL.createObjectURL(new Blob([createForecastReport(forecastResult)], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `FloCast_${forecastResult.direction}_${forecastResult.predictions.at(-1)?.period}_forecast.csv`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const insight = 'Синтетические примеры для проверки интерфейса. Эффекты действий и два основных сценария командой не утверждены.'


  return (
    <div className={`app-shell ${theme}-theme`}>
      <a className="skip-link" href="#main-content">К основному содержанию</a>
      <aside className={`sidebar ${navOpen ? 'is-open' : ''}`} aria-label="Основная навигация">
        <a className="brand-mark" href="#overview" aria-label="FloCast — к обзору">F<span>.</span></a>
        <nav className="main-nav" id="main-navigation">
          {navItems.map(({ id, label, icon: Icon }) => (
            <a key={id} href={`#${id}`} className={activeNav === id ? 'active' : ''} aria-label={label} title={label} aria-current={activeNav === id ? 'location' : undefined} onClick={() => setNavOpen(false)}>
              <Icon size={21} strokeWidth={1.7} /><span>{label}</span>
            </a>
          ))}
        </nav>
        <button ref={menuButton} className="icon-button mobile-menu" aria-controls="main-navigation" onClick={() => setNavOpen(!navOpen)} aria-label={navOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={navOpen}><Menu size={21} /></button>
        <a className="sidebar-info" href="#about" aria-label="О проекте" title="О проекте"><Info size={21} /></a>
      </aside>
      <main className="dashboard" id="main-content" tabIndex={-1}>
        <header className="topbar">
          <a href="#overview" className="wordmark">Flo<span>Cast</span><small>ГРУЗОВЫЕ КОРИДОРЫ — 2030</small></a>
          <div className="topbar-actions"><span className="tag"><i />{forecastResult ? 'ML-ПРОГНОЗ · СИНТЕТИЧЕСКИЕ ПРИМЕРЫ' : hasDemo ? 'ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ' : 'ДАННЫЕ НЕ ПОДКЛЮЧЕНЫ'}</span>
            <button className="icon-button theme-toggle" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}>{theme === 'light' ? <Moon size={20} /> : <Sun size={20} />}</button>
          </div>
        </header>

        <section className="hero" id="overview" aria-labelledby="hero-title">
          <img className="hero-photo" src={`${import.meta.env.BASE_URL}images/sea.jpg`} alt="Контейнерное судно у причала" fetchPriority="high" />
          <div className="hero-shade" />
          <svg className="hero-orbits" viewBox="0 0 600 500" fill="none" aria-hidden="true"><path d="M0 360C180 80 400 100 610 250M0 420C150 170 450 110 620 310M0 480C160 240 460 180 640 380" /><circle cx="285" cy="200" r="4" /><circle cx="470" cy="225" r="3" /></svg>
          <div className="hero-content"><p className="eyebrow">Интеллектуальная транспортная аналитика</p>
            <div className="hero-logo">FloCast<span>.</span></div>
            <h1 id="hero-title">Исследуем грузопотоки.<br /><em>Моделируем будущее.</em></h1>
            <p className="hero-description">Калининградская область, Приморский край и Камчатка: исследуем связи с остальной Россией.</p>
            <div className="hero-actions"><a className="button-primary" href="#analytics">Исследовать данные <ArrowRight size={17} /></a><a className="button-secondary" href="#forecast">Посмотреть демо <ArrowUpRight size={17} /></a></div>
          </div>
        </section>

        <section className="section" aria-labelledby="metrics-title">
          <div className="section-heading"><div><p className="eyebrow">Контрольная панель</p><h2 id="metrics-title">Система в цифрах</h2><p>{selectedRoute.from.label} → {selectedRoute.to.label}</p></div><span className="section-number">ОБЗОР</span></div>
          <div className="kpi-grid">
            {[{ title: 'Объём грузоперевозок', value: hasDemo ? formatNumber(result.volume) : '—', unit: 'тыс. т/сутки', delta: result.volumeDelta, icon: Box }, { title: 'Время доставки', value: hasDemo ? formatNumber(result.time) : '—', unit: 'ч', delta: result.timeDelta, icon: Clock3 }, { title: 'Доступность направлений', value: hasDemo ? `+${result.access}` : '—', unit: '%', delta: result.access, icon: MapPin }, { title: 'Загрузка терминала', value: hasDemo ? String(result.terminal) : '—', unit: '%', delta: result.terminalDelta, icon: Layers3 }].map(({ title, value, unit, delta, icon: Icon }, index) => (
              <article className="kpi-card" key={title}><div className="kpi-top"><span>{title}</span><Icon size={19} /></div><div className="kpi-value">{value}<small>{unit}</small></div><div className="kpi-bottom">{hasDemo && <Delta value={delta} inverse={index === 1} suffix={index === 3 ? ' п.п.' : '%'} />}<span>{hasDemo ? 'к 2025 году · демо' : 'Нет подключённых данных'}</span></div></article>
            ))}
          </div>

        </section>

        <section className="section" id="analytics" aria-labelledby="analytics-title">
          <div className="section-heading"><div><p className="eyebrow">Данные и визуализация</p><h2 id="analytics-title">Потоки. Динамика. Структура.</h2><p>Выберите исследуемый регион, место отправления и место доставки.</p></div><span className="section-number">АНАЛИТИКА</span></div>
          <div className="filterbar"><div className="panel-top"><h3>Параметры анализа <span className="tag">{hasDemo ? 'ДЕМО' : 'ГЕОГРАФИЯ'}</span></h3><button className="text-button" onClick={reset}><RotateCcw size={14} />Сбросить</button></div>
            <GeographyFilters value={draftGeo} direction={draft.direction} onChange={setDraftGeo} onDirection={(direction) => setDraft({ ...draft, direction })} />
            <div className="filters geography-actions">
              <FilterSelect disabled={!isDemoAvailable(draftGeo)} label="Синтетический пример" value={draft.scenario} onChange={(scenario) => setDraft({ ...draft, scenario: scenario as Scenario })} options={SCENARIOS.map((scenario) => ({ value: scenario.id, label: scenario.title }))} />
              <div className="field"><span>Покрытие примера</span><div className="static-field">{isDemoAvailable(draftGeo) ? 'Море · демо · 2025–2030' : 'Детализация не подключена'}</div></div>
              <button className="button-primary" onClick={calculate}>Применить выбор <ArrowRight size={16} /></button>
            </div>{dirty && <p className="filter-status" role="status">Параметры изменены — нажмите «Применить выбор»</p>}
          </div>
          <div className={`coverage-note ${hasDemo ? 'is-demo' : 'is-unavailable'}`} role="status">{coverageMessage}</div>
          {hasDemo ? <div className="charts-grid">
            <article className="panel"><div className="panel-top"><div><h3>Динамика грузопотока</h3><p>2025–2030 · тыс. т/сутки · синтетика</p></div><span className="tag">ДЕМО</span></div><DynamicsChart data={result.dynamics} light={theme === 'light'} /></article>
            <article className="panel"><div className="panel-top"><div><h3>Объём грузоперевозок</h3><p>2025 / 2030 · тыс. т/сутки · {scenarioTitle}</p></div><span className="tag">МОРЕ</span></div><VolumeChart data={result.stacked} light={theme === 'light'} /></article>
          </div> : unavailable}
        </section>

        <TransportGallery />

        <section className="section" id="routes" aria-labelledby="routes-title">
          <div className="section-heading"><div><p className="eyebrow">География грузопотоков</p><h2 id="routes-title">Откуда. Куда. На карте.</h2><p>{corridorLabel} ↔ остальная Россия</p></div><span className="section-number">МАРШРУТЫ</span></div>
          <div className="routes-layout"><div className="map-shell"><iframe title={`Карта выбранного коридора: ${corridorLabel}`} loading="lazy" src={selectedMap.embed} /><a className="map-link" href={selectedMap.full} target="_blank" rel="noreferrer">Открыть карту <ArrowUpRight size={14} /></a></div>
            <article className="panel route-card"><p className="eyebrow">Коридор исследования</p><h3>{selectedRoute.from.label}<span className="route-arrow">↓</span>{selectedRoute.to.label}</h3><div className="route-status"><MapPin size={18} /><span>{hasDemo ? 'Море · синтетический пример' : 'Нет данных для выбранной детализации'}<br />Точки на карте — выбранный город или центр исследуемого региона, не транспортный маршрут.</span></div><button className="button-secondary route-direction-button" onClick={() => {
              const next = { ...applied, direction: applied.direction === 'inbound' ? 'outbound' as const : 'inbound' as const }
              setDraft(next); setApplied(next); setDraftGeo({ ...appliedGeo })
            }}>Поменять направление</button></article>
          </div>
        </section>

        <section className="section" id="forecast" aria-labelledby="forecast-title">
          <div className="section-heading"><div><p className="eyebrow">Прогнозный контур</p><h2 id="forecast-title">Взгляд на горизонт.</h2><p>Экспериментальная годовая линия Усть-Луга — Балтийск, 2024–2030.</p></div><span className="section-number">ПРОГНОЗ</span></div>
          <div className="forecast-banner"><div><span className="tag">{forecastResult ? 'ПРОГНОЗ · ПРОВЕРЕННЫЙ АРТЕФАКТ' : 'МОДЕЛЬ НЕ ПОДКЛЮЧЕНА'}</span><h3>Годовой прогноз перевезённой массы</h3><p>История 2015–2023, тонны за год. Это не синтетический пример выше и не весь грузовой коридор. Точность до 2030 года не подтверждена.</p></div><button className="button-secondary" onClick={exportForecast} disabled={!forecastResult || forecastLoading}><Download size={17} />Экспорт прогноза CSV</button></div>
          <div className="filterbar">
            <div className="filters geography-actions">
              <FilterSelect label="Направление прогноза" value={forecastDraft.direction} onChange={(direction) => setForecastDraft({ ...forecastDraft, direction: direction as Direction })} options={(['inbound', 'outbound'] as Direction[]).map(direction => ({ value: direction, label: DIRECTION_LABELS[direction] }))} />
              <FilterSelect label="Конечный год" value={String(forecastDraft.end_year)} onChange={(year) => setForecastDraft({ ...forecastDraft, end_year: Number(year) })} options={[2024, 2025, 2026, 2027, 2028, 2029, 2030].map(year => ({ value: String(year), label: String(year) }))} />
              <button className="button-secondary" onClick={() => setForecastRetry(value => value + 1)} disabled={forecastLoading}><RotateCcw size={16} />Обновить прогноз</button>
            </div>
            <p className="filter-status" role="status" aria-live="polite" aria-busy={forecastLoading}>{forecastStatus}</p>
          </div>
          <div className="kpi-grid">
            <article className="kpi-card"><div className="kpi-top"><span>Прогноз на {lastForecast?.period ?? forecastDraft.end_year}</span><Box size={19} /></div><div className="kpi-value">{lastForecast ? formatNumber(lastForecast.value) : '—'}<small>т/год</small></div><div className="kpi-bottom">Перевезённая масса линии</div></article>
            <article className="kpi-card"><div className="kpi-top"><span>История модели</span><Clock3 size={19} /></div><div className="kpi-value">{forecastResult?.observations ?? '—'}<small>годовых наблюдений</small></div><div className="kpi-bottom">{forecastResult ? `${forecastResult.training_period.start}–${forecastResult.training_period.end}` : 'Модель недоступна'}</div></article>
            <article className="kpi-card"><div className="kpi-top"><span>Метод</span><MapPin size={19} /></div><div className="kpi-value">{forecastResult?.selected_model ?? '—'}</div><div className="kpi-bottom">Выбор на 2020–2021</div></article>
            <article className="kpi-card"><div className="kpi-top"><span>MAE проверки</span><Layers3 size={19} /></div><div className="kpi-value">{forecastResult?.metrics.selected.holdout.MAE != null ? formatNumber(forecastResult.metrics.selected.holdout.MAE) : '—'}<small>т</small></div><div className="kpi-bottom">2022–2023 · горизонт один год</div></article>
          </div>
          <div className="charts-grid">
            <article className="panel"><div className="panel-top"><div><h3>Перевезённая масса</h3><p>Прогноз · т/год</p></div><span className="tag">FORECAST</span></div><ForecastVolumeChart result={forecastResult} light={theme === 'light'} /></article>
            <article className="panel"><div className="panel-top"><div><h3>Динамика и неопределённость</h3><p>Условный интервал 95%</p></div><span className="tag">FORECAST</span></div><ForecastDynamicsChart result={forecastResult} light={theme === 'light'} /></article>
            <article className="panel"><div className="panel-top"><div><h3>Качество и baseline</h3><p>MAE на 2022–2023, т</p></div><span className="tag">BACKTEST</span></div><ModelQualityChart result={forecastResult} light={theme === 'light'} /></article>
          </div>
          {forecastResult && <article className="panel forecast-table"><div className="panel-top"><h3>Значения прогноза</h3><span className="tag">т/год</span></div><div className="table-scroll"><table><thead><tr><th>Год</th><th>Прогноз, т</th><th>Нижняя 95%, т</th><th>Верхняя 95%, т</th></tr></thead><tbody>{forecastResult.predictions.map(point => <tr key={point.period}><td>{point.period}</td><td>{formatNumber(point.value)}</td><td>{formatNumber(point.interval['95'].lower)}</td><td>{formatNumber(point.interval['95'].upper)}</td></tr>)}</tbody></table></div></article>}
        </section>

        <section className="section" id="scenarios" aria-labelledby="scenarios-title">
          <div className="section-heading"><div><p className="eyebrow">Сценарный анализ</p><h2 id="scenarios-title">Что, если изменить условия?</h2></div><span className="section-number">СЦЕНАРИИ</span></div>
          <div className="scenario-layout"><div className="scenario-options">{SCENARIOS.map((scenario) => <button disabled={!hasDemo} key={scenario.id} className={`scenario-option ${applied.scenario === scenario.id ? 'selected' : ''}`} aria-pressed={applied.scenario === scenario.id} onClick={() => { setDraft({ ...applied, scenario: scenario.id }); setApplied({ ...applied, scenario: scenario.id }) }}><span><strong>{scenario.title}</strong><small>{scenario.description}</small></span><ArrowUpRight size={17} /></button>)}<p className="tiny-note">{insight}</p><a className="button-secondary" href="#capacity">Калькулятор мощности <ArrowRight size={16} /></a></div>
          <article className="panel"><div className="panel-top"><div><h3>Сопоставление гипотез</h3><p>2030 · синтетические объём и время доставки</p></div><span className="tag">ДЕМО</span></div>{hasDemo ? <HypothesisChart data={result.hypotheses} timeUnit={result.timeUnit} light={theme === 'light'} /> : unavailable}</article></div>
          <div className="forecast-banner"><div><span className="tag">РЕЕСТР BACKEND</span><h3>Транспортные действия</h3><p>Определения загружаются из `/api/v1/scenarios`. Численный эффект рассчитывается только как чувствительность к вводу пользователя.</p></div><a className="button-secondary" href="#capacity">Калькулятор мощности <ArrowRight size={16} /></a></div>
          {scenarioRegistryError && <p className="chat-error" role="alert">{scenarioRegistryError} <button className="text-button" onClick={() => setForecastRetry(value => value + 1)}>Повторить</button></p>}
          <div className="data-grid">{scenarioDefinitions.filter(scenario => scenario.label).map(scenario => <article className="panel" key={scenario.id}><h3>{scenario.label}. {scenario.name}</h3><p>{scenario.mechanism}</p><p>{scenario.limitation}</p><span className="tag">{scenario.calculation_status}</span></article>)}</div>
        </section>

        <CapacityCalculator />

        <section className="section ai-feature" aria-labelledby="ai-title"><img src={`${import.meta.env.BASE_URL}images/logistics.jpg`} alt="" loading="lazy" /><div className="ai-feature-shade" /><div className="ai-feature-copy"><p className="eyebrow">Помощник исследователя</p><h2 id="ai-title">Больше контекста.<br /><em>Больше понимания.</em></h2><p>Обсудите транспортную связанность, ограничения данных и вопросы для дальнейшего исследования.</p><div className="ai-feature-tags"><span>Данные и источники</span><span>Транспорт</span><span>Гипотезы</span></div><button className="button-primary" onClick={() => setAssistantOpen(true)}><Sparkles size={17} />Открыть AI-ассистента</button><p className="tiny-note">Ассистент работает через backend проекта. Ответы формируются по переданному контексту.</p></div></section>

        <section className="section" id="data" aria-labelledby="data-title"><div className="section-heading"><div><p className="eyebrow">Прозрачность исследования</p><h2 id="data-title">Данные и их границы.</h2></div><span className="section-number">ДАННЫЕ</span></div><div className="data-grid">
          <article className="panel"><Database size={23} /><h3>Источник и версии прогноза</h3>{forecastResult ? <><p>{forecastSource?.organization}</p>{forecastSource && <p><a href={forecastSource.url} target="_blank" rel="noreferrer">Открыть источник</a></p>}<p>Модель <code>{forecastResult.model_version}</code></p><p>Данные <code>{forecastResult.data_version}</code></p><p>{forecastResult.geography.scope}</p>{forecastResult.warnings.map(warning => <p key={warning}>{warning}</p>)}</> : <p>Источник появится после успешной проверки артефакта. При ошибке значения не подставляются.</p>}</article>
          <article className="panel"><Database size={23} /><h3>Происхождение демо-чисел</h3><p>Все числа аналитического примера вымышлены и имеют статус synthetic.</p><button className="text-button" onClick={exportReport}>Скачать пример и описание <Download size={15} /></button></article>
          <article className="panel"><Info size={23} /><h3>Покрытие</h3><p>{coverageMessage} Выбор города не создаёт наблюдений по нему.</p></article>
          <article className="panel"><CheckCircle2 size={23} /><h3>Следующий шаг</h3><p>Команде предстоит выбрать целевой ряд, утвердить две гипотезы и параметры, проверить источники и принять результаты временной проверки модели.</p></article>
        </div></section>
        <section className="about-section" id="about"><div className="wordmark">Flo<span>Cast</span><small>КОМАНДА NOT FOUND</small></div></section>
        <footer className="site-footer"><span>© 2026 FloCast · Not Found</span><a href="#overview">Наверх ↑</a></footer>
      </main>
      <button className="assistant-launcher button-primary" onClick={() => setAssistantOpen(true)} aria-haspopup="dialog"><Sparkles size={22} /><span>AI-ассистент<small>Обсудить исследование</small></span><ArrowUpRight size={17} /></button>

      {assistantOpen && <button className="assistant-backdrop" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента" />}

      <aside className={`assistant-panel ${assistantOpen ? 'is-open' : ''}`} aria-label="AI-ассистент по годовому прогнозу линии">
        <header className="assistant-header">
          <Sparkles size={22} />
          <div>
            <h2>AI-ассистент</h2>
            <p>Годовой прогноз · через backend</p>
          </div>
          <button className="assistant-close" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента"><X size={24} /></button>
        </header>

        <div className="assistant-context">
          <div><MapPin size={15} /><span>Выбрано: {selectedRoute.from.label} → {selectedRoute.to.label}</span></div>
          {assistantSupported ? <>
            <p>Запрошен годовой прогноз линии Усть-Луга — Балтийск, {DIRECTION_LABELS[applied.direction]}, 2024–2030 · т/год. Это часть коридора; городская детализация отсутствует.</p>
            {visibleChatContext && <details className="assistant-provenance"><summary>Охват, источники и ограничения контекста</summary>
              <p>{visibleChatContext.geography.origin} → {visibleChatContext.geography.destination}. {visibleChatContext.geography.scope}</p>
              <p>Статус: прогноз (forecast); точка выдачи: {visibleChatContext.forecast_origin}. История: {visibleChatContext.training_period.start}–{visibleChatContext.training_period.end}.</p>
              <p>Модель: <code>{visibleChatContext.model_version}</code><br />Данные: <code>{visibleChatContext.data_version}</code></p>
              {visibleChatContext.provenance.sources.map(source => <p key={source.source_id}><a href={source.url} target="_blank" rel="noreferrer">{source.organization}</a> · {source.source_id}</p>)}
              {visibleChatContext.warnings.map(warning => <p key={warning}>{warning}</p>)}
            </details>}
          </> : <p role="status">Для выбранной географии и детализации контекст не подключён.</p>}
        </div>

        <div ref={chatMessagesRef} className="chat-messages" aria-live="polite" aria-label="История диалога">
          {visibleChatHistory.length === 0 && !visibleChatError && (
            <div className="chat-welcome">
              <Sparkles size={28} />
              <p>{chatLoading ? 'Формирую краткую сводку…' : assistantSupported ? 'Здравствуйте' : 'Нет подключённых данных'}</p>
              <span>{!assistantSupported
                ? 'Выберите Калининградскую область без города и «Вся остальная Россия» с другой стороны. Доступна только узкая морская линия.'
                : chatLoading ? 'Ассистент интерпретирует проверенный артефакт прогноза; синтетические примеры не передаются.'
                  : 'Сводка годового прогноза загружается автоматически. Ответы сохраняют ограничения исходной модели.'}</span>
            </div>
          )}
          {visibleChatHistory.map((message, index) => (
            <div key={index} className={`chat-message ${message.role}`}>
              <div
                className="chat-message-content"
                dangerouslySetInnerHTML={{
                  __html:
                    message.role === 'assistant'
                      ? formatAnswer(message.content)
                      : escapeHtml(message.content),
                }}
              />
              {message.role !== 'system' && (
                <span className="chat-message-time">{message.createdAt}</span>
              )}
            </div>
          ))}
          {chatLoading && (
            <div className="chat-loading" aria-label="Ассистент печатает">
              <span className="chat-loading-dot" />
              <span className="chat-loading-dot" />
              <span className="chat-loading-dot" />
            </div>
          )}
        </div>

        {(visibleChatError || apiError) && <div className="chat-error" role="alert">{visibleChatError || apiError}{apiError && <button className="text-button" onClick={() => setConfigRetry(value => value + 1)}>Повторить подключение</button>}</div>}

        <div className="chat-footer">
          <button
            className="chat-clear"
            type="button"
            onClick={clearChat}
          >
            Хотите начать заново? <span>Очистить чат</span>
          </button>
          <form className="chat-input-area" onSubmit={handleAsk}>
            <Paperclip size={18} />
            <input
              className="chat-input"
              type="text"
              value={questionInput}
              onChange={(event) => setQuestionInput(event.target.value)}
              placeholder={!assistantSupported ? 'Контекст не подключён' : apiBase ? 'Введите сообщение' : 'API не настроен'}
              disabled={!apiBase || !assistantSupported || !currentChat || chatLoading}
              aria-label="Вопрос ассистенту"
              maxLength={4000}
            />
            <button
              className="chat-submit"
              type="submit"
              disabled={!apiBase || !assistantSupported || !currentChat || chatLoading || questionInput.trim().length === 0}
              aria-label="Отправить вопрос"
            >
              <ArrowUp size={18} />
            </button>
          </form>
          <span className="chat-hint">Ответ формируется по переданному контексту. Это не наблюдение, прогноз или сценарный расчёт.</span>
        </div>
      </aside>
    </div>
  )
}
