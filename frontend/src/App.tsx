import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight, ArrowUpRight, BarChart3, Box, CheckCircle2, Clock3,
  Database, Download, Info, Layers3, LayoutDashboard, MapPin,
  Menu, Moon, RotateCcw, Route, Send, Sparkles, Sun, Truck, X,
} from 'lucide-react'
import { DynamicsChart, HypothesisChart, VolumeChart } from './ChartPanels'
import {
  DEFAULT_CONFIG,
  SCENARIOS,
  selectDemoScenario,
  formatNumber,
  signed,
  type ForecastConfig,
  type Scenario,
} from './dashboard-data'
import { createDemoReport } from './report'
import { TransportGallery } from './TransportGallery'
import { FilterSelect } from './FilterSelect'
import { GeographyFilters } from './GeographyFilters'
import { DEFAULT_GEOGRAPHY, CORRIDORS, isDemoAvailable, mapLinks, routeSelection, selectionReport, type Geography } from './geography'
import './App.css'

const copyConfig = (config: ForecastConfig): ForecastConfig => ({ ...config })
type Theme = 'dark' | 'light'
type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string }

const navItems = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'analytics', label: 'Аналитика', icon: BarChart3 },
  { id: 'transport', label: 'Транспорт', icon: Truck },
  { id: 'routes', label: 'Маршруты', icon: Route },
  { id: 'forecast', label: 'Прогноз', icon: ArrowUpRight },
  { id: 'scenarios', label: 'Сценарии', icon: Layers3 },
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
    ? 'Доступен синтетический пример Калининградского коридора. Проверенные наблюдения и модель не подключены.'
    : 'Данные и модель для выбранного направления не подключены. Городской или региональный прогноз пока недоступен.'
  const unavailable = <div className="coverage-empty"><MapPin size={25} /><h3>Нет данных для выбранной детализации</h3><p>{coverageMessage}</p></div>
  const scenarioTitle = SCENARIOS.find((scenario) => scenario.id === applied.scenario)?.title ?? 'Базовый сценарий'

  const [apiBase, setApiBase] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [questionInput, setQuestionInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const summaryRequestedRef = useRef(false)
  const chatMessagesRef = useRef<HTMLDivElement>(null)

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
    if (!assistantOpen || !apiBase) return
    summaryRequestedRef.current = true
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
  }, [assistantOpen, apiBase, callAssistant])

  useEffect(() => {
    if (assistantOpen && apiBase && chatHistory.length === 0 && !summaryRequestedRef.current && !chatError) {
      fetchSummary()
    }
  }, [assistantOpen, apiBase, chatHistory.length, chatError, fetchSummary])

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
          <div className="topbar-actions"><span className="tag"><i />{hasDemo ? 'ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ' : 'ДАННЫЕ НЕ ПОДКЛЮЧЕНЫ'}</span>
            <button className="icon-button theme-toggle" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}>{theme === 'light' ? <Moon size={20} /> : <Sun size={20} />}</button>
          </div>
        </header>

        <section className="hero" id="overview" aria-labelledby="hero-title">
          <img className="hero-photo" src="./images/sea.jpg" alt="Контейнерное судно у причала" fetchPriority="high" />
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
          <div className="section-heading"><div><p className="eyebrow">Прогнозный контур</p><h2 id="forecast-title">Взгляд на горизонт.</h2></div><span className="section-number">ПРОГНОЗ</span></div>
          <div className="forecast-banner"><div><span className="tag">МОДЕЛЬ НЕ ПОДКЛЮЧЕНА</span><h3>От данных к обоснованному прогнозу</h3></div><button className="button-secondary" onClick={exportReport}><Download size={17} />{hasDemo ? 'Экспорт демо CSV' : 'Экспорт выбора CSV'}</button></div>
        </section>

        <section className="section" id="scenarios" aria-labelledby="scenarios-title">
          <div className="section-heading"><div><p className="eyebrow">Сценарный анализ</p><h2 id="scenarios-title">Что, если изменить условия?</h2></div><span className="section-number">СЦЕНАРИИ</span></div>
          <div className="scenario-layout"><div className="scenario-options">{SCENARIOS.map((scenario) => <button disabled={!hasDemo} key={scenario.id} className={`scenario-option ${applied.scenario === scenario.id ? 'selected' : ''}`} aria-pressed={applied.scenario === scenario.id} onClick={() => { setDraft({ ...applied, scenario: scenario.id }); setApplied({ ...applied, scenario: scenario.id }) }}><span><strong>{scenario.title}</strong><small>{scenario.description}</small></span><ArrowUpRight size={17} /></button>)}<p className="tiny-note">{insight}</p><a className="button-secondary" href="/capacity">Калькулятор мощности <ArrowRight size={16} /></a></div>
          <article className="panel"><div className="panel-top"><div><h3>Сопоставление гипотез</h3><p>2030 · синтетические объём и время доставки</p></div><span className="tag">ДЕМО</span></div>{hasDemo ? <HypothesisChart data={result.hypotheses} timeUnit={result.timeUnit} light={theme === 'light'} /> : unavailable}</article></div>
        </section>

        <section className="section ai-feature" aria-labelledby="ai-title"><img src="./images/logistics.jpg" alt="" loading="lazy" /><div className="ai-feature-shade" /><div className="ai-feature-copy"><p className="eyebrow">Помощник исследователя</p><h2 id="ai-title">Больше контекста.<br /><em>Больше понимания.</em></h2><p>Обсудите транспортную связанность, ограничения данных и вопросы для дальнейшего исследования.</p><div className="ai-feature-tags"><span>Данные и источники</span><span>Транспорт</span><span>Гипотезы</span></div><button className="button-primary" onClick={() => setAssistantOpen(true)}><Sparkles size={17} />Открыть AI-ассистента</button><p className="tiny-note">Ассистент работает через backend проекта. Ответы формируются по переданному контексту.</p></div></section>

        <section className="section" id="data" aria-labelledby="data-title"><div className="section-heading"><div><p className="eyebrow">Прозрачность исследования</p><h2 id="data-title">Данные и их границы.</h2></div><span className="section-number">ДАННЫЕ</span></div><div className="data-grid">
          <article className="panel"><Database size={23} /><h3>Происхождение чисел</h3><button className="text-button" onClick={exportReport}>Скачать пример и описание <Download size={15} /></button></article>
          <article className="panel"><Info size={23} /><h3>Покрытие</h3><p>{coverageMessage} Выбор города не создаёт наблюдений по нему.</p></article>
          <article className="panel"><CheckCircle2 size={23} /><h3>Следующий шаг</h3><p>Команде предстоит выбрать целевой ряд, утвердить две гипотезы и параметры, проверить источники и принять результаты временной проверки модели.</p></article>
        </div></section>
        <section className="about-section" id="about"><div className="wordmark">Flo<span>Cast</span><small>КОМАНДА NOT FOUND</small></div></section>
        <footer className="site-footer"><span>© 2026 FloCast · Not Found</span><a href="#overview">Наверх ↑</a></footer>
      </main>
      <button className="assistant-launcher button-primary" onClick={() => setAssistantOpen(true)} aria-haspopup="dialog"><Sparkles size={22} /><span>AI-ассистент<small>Обсудить исследование</small></span><ArrowUpRight size={17} /></button>

      {assistantOpen && <button className="assistant-backdrop" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента" />}

      <aside className={`assistant-panel ${assistantOpen ? 'is-open' : ''}`} aria-label="AI-ассистент по данным дашборда">
        <header className="assistant-header">
          <Sparkles size={22} />
          <div>
            <h2>AI-ассистент</h2>
            <p>Подключён к основному backend</p>
          </div>
          <div className="assistant-header-actions">
            <button className="assistant-icon-button assistant-close" type="button" onClick={() => setAssistantOpen(false)} aria-label="Закрыть AI-ассистента"><X size={24} /></button>
          </div>
        </header>

        <div className="assistant-context">
          <div><MapPin size={15} /><span>{selectedRoute.from.label} → {selectedRoute.to.label}</span><span className="context-separator">·</span><span>2030</span></div>
          <p>{hasDemo ? 'Море · синтетический пример' : 'Данные не подключены'}</p>
        </div>

        <div ref={chatMessagesRef} className="chat-messages" aria-live="polite" aria-label="История диалога">
          {chatHistory.length === 0 && !chatError && (
            <div className="chat-welcome">
              <Sparkles size={28} />
              <p>{isLoading ? 'Формирую краткую сводку…' : 'Здравствуйте'}</p>
              <span>{isLoading
                ? 'Ассистент отвечает по контексту из JSON-файлов backend'
                : 'Готов ответить по данным дашборда. Сводка загружается автоматически.'}</span>
            </div>
          )}
          {chatHistory.map((message, index) => (
            <div
              key={index}
              className={`chat-message ${message.role}`}
              dangerouslySetInnerHTML={{
                __html:
                  message.role === 'assistant'
                    ? formatAnswer(message.content)
                    : escapeHtml(message.content),
              }}
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
