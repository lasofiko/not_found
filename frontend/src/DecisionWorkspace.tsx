import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowRight, FileText, Info, Sparkles, X } from 'lucide-react'
import {
  analysisSchema, askSchema, comparisonExport, comparisonRequest, coverageSchema, profileSchema, safeSourceUrl, snapshotSchema,
  type Analysis, type ComparisonInput, type ComparisonSnapshot, type Coverage, type DirectionProfile, type Passport,
} from './comparison-api'
import './DecisionWorkspace.css'

const formatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const text = (value: unknown): string => value == null ? 'Сведения отсутствуют' : typeof value === 'object' ? JSON.stringify(value) : String(value)
const number = (value: number | null | undefined, factor = 1) => value == null ? 'Нет данных' : formatter.format(value / factor)
const directions: Record<string, string> = { inbound: 'Ввоз', outbound: 'Вывоз', both: 'Ввоз и вывоз', none: 'Без направленной разбивки' }
const statuses: Record<string, string> = { scenario: 'Сценарный расчёт', forecast: 'Прогноз', observed: 'Наблюдение', estimated: 'Косвенная оценка', user_assumption: 'Допущение пользователя', derived_forecast: 'Расчёт по прогнозу', derived_observed: 'Расчёт по наблюдениям', mixed: 'Смешанные статусы', unknown: 'Статус не установлен', context: 'Контекстный показатель', 'observed / forecast': 'Наблюдения и прогноз, показаны раздельно', 'observed / требует сверки': 'Наблюдения; требуется сверка' }
const units: Record<string, string> = { tonnes: 'тонны за указанный период', share: 'доля', ratio: 'отношение', thousand_tonnes: 'тысячи тонн за указанный период', unknown: 'Единица не установлена', mixed: 'Разные единицы; см. источник' }
const colors = ['#8A8DA4', '#705FD0', '#207D75']
const quickQuestions = ['Объясни различия вариантов', 'При каких условиях изменения достаточно?', 'Какие допущения сильнее всего влияют на вывод?', 'Чего не хватает для практического решения?', 'Подготовь краткое пояснение']

export function NumberPassport({ passport, onClose }: { passport: Passport; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = dialog.current
    if (!panel) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const background: HTMLElement[] = []
    let branch: HTMLElement = panel
    while (branch.parentElement) {
      const parent = branch.parentElement
      for (const element of parent.children) if (element instanceof HTMLElement && element !== branch && !element.hasAttribute('data-passport-overlay')) background.push(element)
      if (parent === document.body) break
      branch = parent
    }
    const oldInert = background.map(element => element.hasAttribute('inert'))
    background.forEach(element => element.setAttribute('inert', ''))
    const items = () => Array.from(panel.querySelectorAll<HTMLElement>('button, a[href], [tabindex="0"]'))
    items()[0]?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose() }
      if (event.key === 'Tab') {
        const controls = items(); const first = controls[0]; const last = controls.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    const focus = (event: FocusEvent) => { if (event.target instanceof Node && !panel.contains(event.target)) items()[0]?.focus() }
    panel.addEventListener('keydown', keydown)
    document.addEventListener('focusin', focus)
    return () => {
      panel.removeEventListener('keydown', keydown); document.removeEventListener('focusin', focus)
      background.forEach((element, index) => { if (!oldInert[index]) element.removeAttribute('inert') })
      document.body.style.overflow = previousOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [onClose])
  const url = safeSourceUrl(passport.source_url)
  const rows: [string, unknown][] = [
    ['Определение', passport.definition], ['Значение', `${typeof passport.value === 'number' ? number(passport.value) : text(passport.value)} ${units[passport.unit] ?? passport.unit}`], ['Период', passport.period],
    ['География и охват', passport.geography], ['Направление', directions[passport.direction] ?? passport.direction],
    ['Тип результата', statuses[passport.result_type] ?? passport.result_type], ['Источник', passport.source],
    ['Исходная единица', units[passport.original_unit ?? ''] ?? passport.original_unit], ['Пересчёт', passport.conversion], ['Формула', passport.formula],
    ['Версия данных', passport.data_version], ['Версия модели', passport.model_version], ['Точка выдачи прогноза', passport.forecast_origin],
    ['Дата расчёта', passport.calculated_at], ['Машинная проверка', passport.machine_validation],
    ['Человеческая сверка', passport.human_review ?? 'Сведения о завершённой сверке отсутствуют'],
  ]
  return <div className="number-passport-overlay" data-passport-overlay="" onClick={onClose}>
    <div ref={dialog} className="number-passport panel" role="dialog" aria-modal="true" aria-labelledby="number-passport-title" onClick={event => event.stopPropagation()}>
      <header className="panel-top"><h2 id="number-passport-title">{passport.title}</h2><button className="icon-button" onClick={onClose} aria-label="Закрыть паспорт"><X size={20} /></button></header>
      <dl>{rows.map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{text(value)}</dd></div>)}</dl>
      {url ? <a className="text-button" href={url} target="_blank" rel="noreferrer">Открыть разрешённый источник</a> : <p className="tiny-note">Публичная ссылка на источник не предоставлена.</p>}
      <h3>Ограничения</h3><ul>{passport.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
      <p className="tiny-note">Проверка хеша подтверждает идентичность файла, а не правильность методики или завершение человеческой приёмки.</p>
    </div>
  </div>
}

function Metric({ value, reference, passports, onPassport, factor = 1, suffix = '' }: {
  value: number | null; reference: string; passports: Record<string, Passport>; onPassport: (passport: Passport) => void; factor?: number; suffix?: string;
}) {
  const passport = passports[reference]
  return passport ? <button className="decision-number" type="button" onClick={() => onPassport(passport)} title="Источник и расчёт" aria-label={`${passport.title}: ${number(value, factor)}${suffix}. Источник и расчёт`}>{number(value, factor)}{suffix}<Info size={12} aria-hidden="true" /></button> : <span>{number(value, factor)}{suffix}</span>
}

function WarningList({ items }: { items: string[] }) {
  return items.length ? <details className="decision-warnings"><summary>Ограничения и предупреждения ({items.length})</summary><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></details> : null
}

function DirectionView({ profile, factor, onPassport }: { profile: DirectionProfile; factor: number; onPassport: (passport: Passport) => void }) {
  const max = Math.max(1, ...profile.rows.flatMap(row => [row.inbound ?? 0, row.outbound ?? 0]))
  const x = (index: number) => 50 + index * 650 / Math.max(1, profile.rows.length - 1)
  const y = (value: number) => 205 - value / max * 160
  return <div className="panel decision-profile">
    <div className="panel-top"><h3>Ввоз и вывоз на общей шкале</h3><span className="tag">{profile.status === 'ready' ? 'Два направления' : 'Неполное покрытие'}</span></div>
    {!profile.rows.some(row => row.inbound !== null || row.outbound !== null) ? <p>Проверенная модель и согласованная история не подключены. Значения не подставляются.</p> : <>
      <svg className="direction-plot" viewBox="0 0 750 255" role="img" aria-label={`Ввоз и вывоз, ${factor === 1 ? 'тонны' : 'тысячи тонн'} за год. Сплошная линия — история, пунктир — прогноз из 2023 года.`}>
        <line x1="50" y1="205" x2="710" y2="205" stroke="currentColor" opacity=".3" />
        <text x="3" y="45">{number(max, factor)}</text><text x="20" y="208">0</text>
        {(['inbound', 'outbound'] as const).map((direction, d) => <g key={direction}>
          {profile.rows.map((row, index) => {
            const previous = profile.rows[index - 1]
            return index > 0 && row[direction] !== null && previous[direction] !== null ? <line key={row.period} x1={x(index - 1)} y1={y(previous[direction])} x2={x(index)} y2={y(row[direction])} stroke={colors[d + 1]} strokeWidth="3" strokeDasharray={row.result_type === 'forecast' ? '7 5' : undefined} /> : null
          })}
        </g>)}
        {profile.rows.map((row, index) => (index === 0 || index === profile.rows.length - 1 || row.period === '2023') && <text key={row.period} x={x(index)} y="235" textAnchor="middle">{row.period}</text>)}
      </svg>
      <p className="tiny-note">Ввоз — фиолетовый, вывоз — бирюзовый. Сплошная линия — наблюдения до 2023 года; пунктир — прогноз 2024–2030 из точки 2023. Интервалы направлений не суммируются.</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Таблица ввоза и вывоза"><table className="decision-table"><caption>Одинаковые охват, год и единица · {factor === 1 ? 'т' : 'тыс. т'} за год</caption><thead><tr><th scope="col">Год / статус</th><th scope="col">Ввоз</th><th scope="col">Вывоз</th><th scope="col">Сумма</th><th scope="col">Доля ввоза</th><th scope="col">Доля вывоза</th><th scope="col">Разность</th><th scope="col">Ввоз / вывоз</th></tr></thead><tbody>
        {profile.rows.map((row, index) => <tr key={row.period}><th scope="row">{row.period}<small>{statuses[row.result_type] ?? row.result_type}</small></th>{(['inbound', 'outbound', 'total', 'inbound_share', 'outbound_share', 'difference_tonnes', 'inbound_outbound_ratio'] as const).map(field => <td key={field}><Metric value={row[field]} reference={`rows.${index}.${field}`} passports={profile.passports} onPassport={onPassport} factor={field.endsWith('_share') ? .01 : field.endsWith('_ratio') ? 1 : factor} suffix={field.endsWith('_share') ? '%' : ''} /></td>)}</tr>)}
      </tbody></table></div>
    </>}
    <p className="decision-important">Разность масс не показывает свободную вместимость и не доказывает наличие порожних обратных рейсов.</p>
    <WarningList items={profile.warnings} />
  </div>
}

function CoverageView({ coverage }: { coverage: Coverage }) {
  const stages = [['source_registered', 'Источник зарегистрирован'], ['original_available', 'Оригинал доступен'], ['extracted', 'Данные извлечены'], ['series_checked', 'Ряд подготовлен и проверен'], ['model_exists', 'Модель существует'], ['model_connected', 'Модель проверена и подключена'], ['ui_available', 'Результат в интерфейсе']] as const
  return <details className="panel decision-coverage" id="coverage"><summary className="decision-coverage-title">Какие данные доступны · раскрыть матрицу покрытия</summary><p className="tiny-note">Географический справочник не подтверждает наличие грузовых наблюдений. Проверено: {coverage.checked_at}.</p>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Матрица покрытия данных"><table className="decision-table"><caption>Три территории, отдельные объекты и этапы готовности</caption><thead><tr><th scope="col">Территория / показатель</th>{stages.map(([key, label]) => <th scope="col" key={key}>{label}</th>)}<th scope="col">Охват и источник</th></tr></thead><tbody>
      {coverage.rows.map(row => <tr key={row.id}><th scope="row">{row.territory}<small>{row.object} · {row.metric}</small><small>{directions[row.direction ?? 'none'] ?? row.direction} · {{ sea: 'Море', rail_land: 'Сухопутная ж/д', road: 'Автоперевозки', air: 'Авиация' }[row.mode ?? ''] ?? row.mode ?? 'Вид не установлен'} · {{ year: 'Год', month: 'Месяц', annual: 'Год', monthly: 'Месяц', 'year / mixed': 'Годовая и другая частота' }[row.frequency] ?? row.frequency} · {row.period} · {units[row.unit] ?? row.unit}</small><small>{statuses[row.value_status] ?? row.value_status}</small></th>{stages.map(([key]) => <td key={key}><span className={`coverage-stage ${row.stages[key] === true ? 'available' : ''}`}>{row.stages[key] === true ? 'Да' : row.stages[key] === false ? 'Нет' : 'Не установлено'}</span></td>)}<td>{row.source && (safeSourceUrl(row.source.url) ? <a href={safeSourceUrl(row.source.url)!} target="_blank" rel="noreferrer">{row.source.organization ?? row.source.source_id}</a> : <span>{row.source.organization ?? row.source.source_id}</span>)}{row.limitations.map((item, index) => <p key={index}>{item}</p>)}{row.evidence_notes?.map((item, index) => <small key={index}>{item}</small>)}{row.forecast_available ? <a className="text-button" href="#forecast">Перейти в раздел прогноза · направление выбирается отдельно</a> : <small>Переход к прогнозу недоступен: срез не подключён.</small>}</td></tr>)}
    </tbody></table></div><WarningList items={coverage.warnings} />
  </details>
}

export function DecisionAssistant({ snapshot }: { snapshot: ComparisonSnapshot | null }) {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<{ question: string; answer: string; refs: string[] }[]>([])
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const detailed = useRef(false)
  const generation = useRef(0)
  const snapshotRef = useRef(snapshot)
  snapshotRef.current = snapshot
  const cancel = useCallback(() => {
    generation.current += 1; pending.current?.abort(); pending.current = null
    const previous = snapshotRef.current
    if (detailed.current && previous) void comparisonRequest(`/comparisons/${encodeURIComponent(previous.comparison_id)}/analysis/cancel`, value => value, new AbortController().signal, {}).catch(() => {})
    detailed.current = false; setBusy(false)
  }, [])
  useEffect(() => () => { generation.current += 1; pending.current?.abort() }, [])
  const run = async (detail: boolean, supplied?: string) => {
    if (!snapshot || pending.current) return
    const query = (supplied ?? question).trim()
    if (!detail && !query) return
    const id = snapshot.comparison_id; const token = ++generation.current
    const controller = new AbortController(); pending.current = controller; detailed.current = detail
    setBusy(true); setError(''); if (detail) setAnalysis(null)
    try {
      if (detail) {
        const result = await comparisonRequest(`/comparisons/${encodeURIComponent(id)}/analysis`, value => analysisSchema.parse(value), controller.signal, {}, 70000)
        if (result.comparison_id !== id) throw new Error('Ответ относится к другому сравнению.')
        if (!controller.signal.aborted && token === generation.current) setAnalysis(result)
      } else {
        const result = await comparisonRequest(`/comparisons/${encodeURIComponent(id)}/ask`, value => askSchema.parse(value), controller.signal, { question: query }, 70000)
        if (result.comparison_id !== id) throw new Error('Ответ относится к другому сравнению.')
        if (!controller.signal.aborted && token === generation.current) { setMessages(items => [...items, { question: query, answer: result.answer, refs: result.evidence_refs }]); setQuestion('') }
      }
    } catch (cause) { if (!controller.signal.aborted && token === generation.current) setError(cause instanceof Error ? cause.message : 'Не удалось получить пояснение. Повторите запрос.') }
    finally { if (token === generation.current) { pending.current = null; detailed.current = false; setBusy(false) } }
  }
  if (!snapshot) return <div className="decision-assistant"><p>Сначала выполните расчёт в разделе «Сравнить решения». После изменения параметров нужно новое сравнение.</p><a className="text-button" href="#compare">Перейти к сравнению</a></div>
  return <div className="decision-assistant">
    <p className="tiny-note">Сценарный расчёт · {snapshot.context.period} · {directions[snapshot.context.direction]}. Ответ относится только к применённому сравнению. Ассистент не меняет допущения.</p>
    <div className="decision-quick">{quickQuestions.map(item => <button key={item} className="button-secondary" disabled={busy} onClick={() => void run(false, item)}>{item}</button>)}</div>
    <form onSubmit={event => { event.preventDefault(); void run(false) }} className="decision-chat-form"><label className="field"><span>Вопрос по выбранному сравнению</span><textarea value={question} onChange={event => setQuestion(event.target.value)} maxLength={4000} rows={3} disabled={busy} required /></label><button className="button-primary" disabled={busy || !question.trim()}>Спросить</button></form>
    <button className="button-secondary" disabled={busy} onClick={() => void run(true)}><Sparkles size={16} />Подробный разбор</button>{busy && <button className="text-button" type="button" onClick={cancel}>Отменить запрос</button>}
    <p role="status" className="tiny-note" aria-busy={busy}>{busy ? 'Готовим пояснение по проверенному снимку…' : 'Обычный вопрос не запускает группу специалистов.'}</p>
    {error && <p role="alert" className="decision-error">{error} Сравнение, источники и отчёт продолжают работать.</p>}
    <div aria-live="polite">{messages.map((message, index) => <article className="decision-answer" key={index}><h3>{message.question}</h3><p>{message.answer}</p><details><summary>Ссылки на данные сравнения</summary><ul>{message.refs.map(ref => <li key={ref}>{snapshot.passports[ref]?.title ?? ref}</li>)}</ul></details></article>)}</div>
    {analysis && <div className="decision-analysis" aria-live="polite"><p className="decision-important">{analysis.status === 'complete' ? 'Подробный разбор получен' : analysis.status === 'partial' ? 'Частичный разбор: часть специалистов недоступна' : 'Разбор недоступен. Повторите запрос.'}</p>
      {analysis.sections.map((section, index) => <section key={index}><h3>{section.title}</h3><ul>{section.items.map((item, n) => <li key={n}>{item}</li>)}</ul></section>)}
      <details><summary>Состояние отдельных разборов и ограничения</summary>{analysis.agents.map(agent => <article key={agent.agent_id}><h3>{{ comparison_analyst: 'Интерпретация сравнения', evidence_analyst: 'Проверка опоры на данные', assumption_reviewer: 'Критика допущений' }[agent.agent_id] ?? 'Специализированный разбор'}</h3><p>{agent.status === 'ready' || agent.status === 'success' || agent.status === 'complete' ? 'Ответ получен' : 'Ответ недоступен или неполон'}</p>{[...agent.findings, ...agent.assumptions, ...agent.limitations, ...agent.questions_for_user].map((item, index) => <p key={index}>{item}</p>)}</article>)}</details>
      <WarningList items={analysis.warnings} /><p className="tiny-note">Несколько вызовов модели не являются независимой научной или человеческой проверкой.</p>
    </div>}
  </div>
}

export function DecisionWorkspace({ onSnapshotChange, onOpenAssistant, initialRegion = 'kaliningrad', initialDirection = 'inbound', supported = true }: {
  onSnapshotChange: (snapshot: ComparisonSnapshot | null) => void; onOpenAssistant: () => void;
  initialRegion?: string; initialDirection?: 'inbound' | 'outbound'; supported?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<ComparisonSnapshot | null>(null)
  const [message, setMessage] = useState('Введите нагрузку, возможность и допущения за один и тот же год.')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [region, setRegion] = useState(initialRegion)
  const [loadBasis, setLoadBasis] = useState<'direct' | 'forecast'>('direct')
  const [factor, setFactor] = useState(1)
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [coverageError, setCoverageError] = useState('')
  const [coverageRetry, setCoverageRetry] = useState(0)
  const [profile, setProfile] = useState<DirectionProfile | null>(null)
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileError, setProfileError] = useState('')
  const [passport, setPassport] = useState<Passport | null>(null)
  const [cell, setCell] = useState<[number, number] | null>(null)
  const [exportBusy, setExportBusy] = useState(false)
  const generation = useRef(0)
  const requests = useRef<AbortController[]>([])
  const form = useRef<HTMLFormElement>(null)
  const profileRequest = useRef<AbortController | null>(null)
  const busyRef = useRef(false)
  const closePassport = useCallback(() => setPassport(null), [])
  const invalidate = () => {
    generation.current++; requests.current.forEach(controller => controller.abort()); requests.current = []; busyRef.current = false
    profileRequest.current?.abort(); profileRequest.current = null; setProfile(null); setProfileBusy(false); setProfileError('')
    setSnapshot(null); onSnapshotChange(null); setBusy(false); setExportBusy(false); setPassport(null); setCell(null); setError('')
    setMessage('Параметры изменены. Предыдущее сравнение неактуально. Выполните расчёт заново.')
  }
  useEffect(() => () => { requests.current.forEach(controller => controller.abort()); profileRequest.current?.abort() }, [])
  useEffect(() => {
    const controller = new AbortController()
    comparisonRequest('/coverage', value => coverageSchema.parse(value), controller.signal).then(value => { if (!controller.signal.aborted) { setCoverage(value); setCoverageError('') } }).catch(cause => { if (!controller.signal.aborted) setCoverageError(cause instanceof Error ? cause.message : 'Матрица недоступна.') })
    return () => controller.abort()
  }, [coverageRetry])
  useEffect(() => {
    if (!snapshot) return
    const controller = new AbortController()
    requests.current.push(controller)
    const token = generation.current
    const verify = () => {
      void comparisonRequest(`/comparisons/${encodeURIComponent(snapshot.comparison_id)}`, value => snapshotSchema.parse(value), controller.signal).catch(cause => {
        if (controller.signal.aborted || token !== generation.current) return
        generation.current++; requests.current.forEach(request => request.abort()); busyRef.current = false
        setSnapshot(null); onSnapshotChange(null); setPassport(null); setBusy(false); setExportBusy(false)
        setMessage('Контекст сравнения требует нового расчёта.')
        setError(cause instanceof Error ? cause.message : 'Не удалось подтвердить актуальность снимка. Выполните сравнение заново.')
      })
    }
    const validationTimer = setInterval(verify, 15000)
    window.addEventListener('focus', verify)
    const timer = setTimeout(() => {
      generation.current++; requests.current.forEach(controller => controller.abort()); setSnapshot(null); onSnapshotChange(null)
      setMessage('Срок действия сравнения истёк. Выполните расчёт заново.'); setPassport(null); setBusy(false); setExportBusy(false); busyRef.current = false
    }, Math.max(0, Date.parse(snapshot.expires_at) - Date.now()))
    return () => { clearTimeout(timer); clearInterval(validationTimer); window.removeEventListener('focus', verify); controller.abort() }
  }, [snapshot, onSnapshotChange])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (busyRef.current || region !== 'kaliningrad' || !supported) return
    if (!event.currentTarget.reportValidity()) return
    const values = new FormData(event.currentTarget)
    const input: ComparisonInput = {
      region, mode: 'sea', direction: String(values.get('direction')) as 'inbound' | 'outbound', period: String(values.get('period')),
      load_basis: loadBasis, ...(loadBasis === 'direct' ? { demand_tonnes: Number(values.get('demand_tonnes')) } : { forecast_multiplier: Number(values.get('forecast_multiplier')) }),
      demand_source: String(values.get('demand_source')).trim(), capacity_tonnes: Number(values.get('capacity_tonnes')), capacity_source: String(values.get('capacity_source')).trim(),
      actions: { reserve_ferry: { delta_tonnes: Number(values.get('reserve_delta')), source: String(values.get('reserve_source')).trim() }, additional_ferry: { delta_tonnes: Number(values.get('additional_delta')), source: String(values.get('additional_source')).trim() } },
      include_directions: values.get('include_directions') === 'on', grid_size: 9,
    }
    const token = ++generation.current; const controller = new AbortController(); requests.current.push(controller); busyRef.current = true
    setBusy(true); setError(''); setSnapshot(null); onSnapshotChange(null); setMessage('Сервер рассчитывает сравнение и пороги…')
    try {
      const result = await comparisonRequest('/comparisons', value => snapshotSchema.parse(value), controller.signal, input)
      if (result.context.period !== input.period || result.context.direction !== input.direction || result.context.region !== input.region) throw new Error('Сервер вернул другой контекст сравнения.')
      if (!controller.signal.aborted && token === generation.current) { setSnapshot(result); onSnapshotChange(result); setMessage('Сравнение применено. Таблица, график, паспорта, экспорт и AI используют один снимок.'); setCell(null) }
    } catch (cause) { if (!controller.signal.aborted && token === generation.current) { setError(cause instanceof Error ? cause.message : 'Расчёт недоступен. Повторите запрос.'); setMessage('Сравнение не выполнено. Проверьте параметры и повторите.') } }
    finally { if (token === generation.current) { busyRef.current = false; setBusy(false) } }
  }
  const loadProfile = async () => {
    profileRequest.current?.abort(); const controller = new AbortController(); profileRequest.current = controller
    setProfileBusy(true); setProfileError('')
    try { const result = await comparisonRequest('/direction-profile?end_year=2030', value => profileSchema.parse(value), controller.signal); if (!controller.signal.aborted) setProfile(result) }
    catch (cause) { if (!controller.signal.aborted) setProfileError(cause instanceof Error ? cause.message : 'Профиль недоступен.') }
    finally { if (!controller.signal.aborted) setProfileBusy(false) }
  }
  const exportResult = async (format: 'html' | 'csv' | 'json') => {
    if (!snapshot || exportBusy) return
    const token = generation.current; const controller = new AbortController(); requests.current.push(controller)
    const reportWindow = format === 'html' ? window.open('', '_blank') : null
    if (reportWindow) { reportWindow.opener = null; reportWindow.document.title = 'Подготовка обоснования'; reportWindow.document.body.textContent = 'Подготавливаем обоснование решения…' }
    setExportBusy(true); setError('')
    try {
      const blob = await comparisonExport(snapshot.comparison_id, format, controller.signal)
      if (controller.signal.aborted || token !== generation.current) { reportWindow?.close(); return }
      const url = URL.createObjectURL(blob)
      if (reportWindow) reportWindow.location.href = url
      else { const link = document.createElement('a'); link.href = url; link.download = `FloCast-${snapshot.comparison_id}.${format}`; link.click(); if (format === 'html') setMessage('Браузер не открыл отдельное окно. HTML-отчёт скачан: откройте файл и нажмите «Печать / Сохранить в PDF».') }
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (cause) { reportWindow?.close(); if (!controller.signal.aborted && token === generation.current) setError(cause instanceof Error ? cause.message : 'Не удалось получить отчёт. Повторите запрос.') }
    finally { if (token === generation.current) setExportBusy(false) }
  }
  const numericField = (name: string, label: string, extra: Record<string, string | number> = {}): ReactNode => <label className="field"><span>{label}</span><input name={name} type="number" min="0" max="1000000000000" step="any" required {...extra} /></label>
  const sourceField = (name: string, label: string): ReactNode => <label className="field"><span>{label}</span><input name={name} type="text" maxLength={2000} pattern=".*\S.*" placeholder="Источник или описание допущения пользователя" required /></label>
  const profileShown = snapshot?.direction_profile ?? profile
  const grid = snapshot?.thresholds.grid
  const unit = factor === 1 ? 'т' : 'тыс. т'
  return <section className="section decision-workspace" id="compare" aria-labelledby="decision-title">
    <div className="section-heading"><div><p className="eyebrow">От данных к решению</p><h2 id="decision-title">Сравнить решения.</h2><p>Выберите срез, проверьте покрытие данных и сравните действия при собственных допущениях.</p></div><span className="tag">Сценарный расчёт</span></div>
    {coverage ? <CoverageView coverage={coverage} /> : <div className="panel"><p role="status">{coverageError || 'Проверяем доступность данных…'}</p>{coverageError && <button className="text-button" onClick={() => setCoverageRetry(value => value + 1)}>Повторить загрузку матрицы</button>}</div>}
    <form ref={form} className="panel decision-form" onSubmit={submit} onInput={invalidate} onChange={event => { if (event.target instanceof HTMLElement && (event.target.tagName === 'SELECT' || event.target.tagName === 'INPUT' && event.target.getAttribute('type') === 'checkbox')) invalidate() }}>
      <h3>Один период и один охват для всех вариантов</h3><p className="tiny-note">Рабочий объект — морская линия Усть-Луга — Балтийск. Прогноз перевезённой массы не измеряет спрос или доступную мощность. Значения мощности не заполняются историческим максимумом.</p>
      <div className="decision-fields">
        <label className="field"><span>Территория</span><select name="region" value={region} onChange={event => setRegion(event.target.value)}><option value="kaliningrad">Калининградская область · линия</option><option value="primorsky">Приморский край · не подключён</option><option value="kamchatka">Камчатский край · не подключён</option></select></label>
        <label className="field"><span>Направление</span><select name="direction" defaultValue={initialDirection}><option value="inbound">Ввоз в область</option><option value="outbound">Вывоз из области</option></select></label>
        <label className="field"><span>Год расчёта</span><input type="number" name="period" min={loadBasis === 'forecast' ? 2024 : 2015} max="2030" step="1" required placeholder="Например, 2027" /></label>
        <label className="field"><span>Как задать сценарную нагрузку</span><select name="load_basis" value={loadBasis} onChange={event => setLoadBasis(event.target.value as 'direct' | 'forecast')}><option value="direct">Ввести тонны напрямую</option><option value="forecast">Выбрать прогноз как масштаб нагрузки</option></select></label>
        {loadBasis === 'direct' ? numericField('demand_tonnes', 'Сценарная нагрузка, т за год') : numericField('forecast_multiplier', 'Множитель выбранного прогноза', { max: 100 })}
        {sourceField('demand_source', 'Происхождение сценарной нагрузки')}
        {numericField('capacity_tonnes', 'Исходная провозная возможность, т за год')}
        {sourceField('capacity_source', 'Происхождение исходной возможности')}
      </div>
      {loadBasis === 'forecast' && <p className="decision-important">Сценарная нагрузка на основе выбранного прогноза с вашим множителем — не наблюдённый спрос. Последний факт артефакта — 2023; годы 2024–2030 остаются прогнозом из точки 2023.</p>}
      <div className="decision-actions"><fieldset><legend>А · Резервный паром на время ремонтов</legend>{numericField('reserve_delta', 'Дополнительная возможность А, т за год')}{sourceField('reserve_source', 'Происхождение допущения А')}</fieldset><fieldset><legend>В · Постоянный дополнительный паром</legend>{numericField('additional_delta', 'Дополнительная возможность В, т за год')}{sourceField('additional_source', 'Происхождение допущения В')}</fieldset></div>
      <p className="tiny-note">Нулевой эффект вводится явно. Численные эффекты действий не подтверждены карточками. А+В недоступен: совместимость и отсутствие пересечения эффектов не определены.</p>
      <label className="decision-check"><input type="checkbox" name="include_directions" />Включить в снимок совместный профиль ввоза и вывоза</label>
      {(region !== 'kaliningrad' || !supported) && <p role="status" className="decision-important">Для выбранной географии или городской детализации направленная модель не подключена. Данные другой линии не подставляются. Выберите поддерживаемый срез без городской детализации.</p>}
      <button className="button-primary" type="submit" disabled={busy || region !== 'kaliningrad' || !supported}>{busy ? 'Рассчитываем…' : 'Сравнить решения'}<ArrowRight size={16} /></button><p className="filter-status" role="status" aria-busy={busy}>{message}</p>
    </form>
    {error && <p className="decision-error" role="alert">{error}</p>}
    <div className="decision-toolbar"><label className="field"><span>Единица отображения</span><select value={factor} onChange={event => setFactor(Number(event.target.value))}><option value={1}>Тонны</option><option value={1000}>Тысячи тонн</option></select></label><p className="tiny-note">Меняется только отображение совместимой массы. Расчёт хранится в тоннах.</p></div>
    {snapshot && <div className="decision-results" aria-live="polite">
      <div className="panel"><div className="panel-top"><h3>{snapshot.context.route} · {snapshot.context.period} · {directions[snapshot.context.direction]}</h3><span className="tag">Сценарный расчёт · {unit}/год</span></div><p className="tiny-note">{snapshot.context.scope} · Расчёт: {snapshot.created_at}. Действует до: {snapshot.expires_at}. Версия модели: {snapshot.context.model_version ?? 'Прогноз не использован'}.</p>
        <div className="table-scroll" role="region" tabIndex={0} aria-label="Числовое сравнение решений"><table className="decision-table"><caption>Сравнение при одной сценарной нагрузке · {unit} за год. Нажмите число, чтобы открыть источник и расчёт.</caption><thead><tr><th scope="col">Вариант</th>{['Нагрузка', 'Исходная возможность', 'Дополнительная возможность', 'Итоговая возможность', 'Непокрытая нагрузка', 'Сокращение непокрытой нагрузки', 'Запас возможности', 'Покрытие нагрузки'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{snapshot.variants.map((variant, index) => <tr key={variant.id}><th scope="row"><span className="decision-variant-dot" style={{ background: colors[index] }} />{variant.label}<small>{variant.name}</small></th>{(['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes', 'scenario_capacity_tonnes', 'scenario_shortfall_tonnes', 'shortfall_reduction_tonnes', 'scenario_spare_capacity_tonnes', 'coverage_share'] as const).map(field => <td key={field}><Metric value={variant[field]} reference={`variants.${index}.${field}`} passports={snapshot.passports} onPassport={setPassport} factor={field === 'coverage_share' ? .01 : factor} suffix={field === 'coverage_share' && variant[field] !== null ? '%' : ''} /></td>)}</tr>)}</tbody></table></div>
        <figure className="decision-bars"><figcaption>Итоговая возможность и непокрытая нагрузка · {unit} за {snapshot.context.period}</figcaption>{snapshot.variants.map((variant, index) => <div key={variant.id} className="decision-bar-row"><span>{variant.label}</span><div className="decision-bar-track"><div style={{ width: `${variant.scenario_capacity_tonnes / Math.max(1, ...snapshot.variants.map(v => Math.max(v.scenario_capacity_tonnes, v.demand_tonnes))) * 100}%`, background: colors[index] }} /></div><span>{number(variant.scenario_capacity_tonnes, factor)} / непокрыто {number(variant.scenario_shortfall_tonnes, factor)}</span></div>)}</figure>
        <details className="decision-warnings"><summary>Принятые допущения для вариантов</summary>{snapshot.variants.map(variant => <p key={variant.id}><strong>{variant.label}:</strong> {variant.assumption_source}</p>)}</details>
        <WarningList items={snapshot.warnings} />
      </div>
      <div className="panel decision-threshold"><h3>При каких условиях этого достаточно?</h3><p>Требуемая дополнительная возможность для покрытия введённой нагрузки: <Metric value={snapshot.thresholds.required_delta_tonnes} reference="thresholds.required_delta_tonnes" passports={snapshot.passports} onPassport={setPassport} factor={factor} /> {unit} за год.</p>
        <p className="tiny-note">Каждая ячейка — непокрытая нагрузка. «Покрыто» обозначает границу нулевого остатка. Выберите ячейку, чтобы увидеть конкретные значения. Измените допущения в форме и пересчитайте, чтобы получить новый снимок.</p>
        {grid && <div className="table-scroll" role="region" tabIndex={0} aria-label="Матрица порогового анализа"><table className="decision-table decision-heatmap"><caption>Столбцы — сценарная нагрузка; строки — дополнительная возможность · {unit}/год</caption><thead><tr><th scope="col">Прирост / нагрузка</th>{grid.demand_values.map((value, index) => <th scope="col" key={index}>{number(value, factor)}</th>)}</tr></thead><tbody>{grid.delta_values.map((delta, row) => <tr key={row}><th scope="row">{number(delta, factor)}</th>{grid.shortfalls[row].map((value, col) => <td key={col}><button type="button" className={value === 0 ? 'is-covered' : 'is-uncovered'} aria-pressed={cell?.[0] === row && cell?.[1] === col} onClick={() => setCell([row, col])}>{value === 0 ? 'Покрыто · 0' : number(value, factor)}</button></td>)}</tr>)}</tbody><tfoot><tr><th scope="row">Порог прироста для покрытия</th>{grid.boundary_delta_tonnes.map((value, index) => <td key={index}>{number(value, factor)}</td>)}</tr></tfoot></table></div>}
        {cell && grid && <div role="status" className="decision-important"><p>Нагрузка: {number(grid.demand_values[cell[1]], factor)} {unit}; дополнительная возможность: {number(grid.delta_values[cell[0]], factor)} {unit}; непокрытая нагрузка: {number(grid.shortfalls[cell[0]][cell[1]], factor)} {unit}. Исходная возможность: {number(snapshot.variants[0].capacity_tonnes, factor)} {unit} за тот же год.</p>{snapshot.passports[`thresholds.grid.shortfalls.${cell[0]}.${cell[1]}`] && <button className="text-button" onClick={() => setPassport(snapshot.passports[`thresholds.grid.shortfalls.${cell[0]}.${cell[1]}`])}>Источник и расчёт выбранной ячейки</button>}</div>}
        <p className="tiny-note">Максимальная покрываемая нагрузка каждого варианта равна его итоговой возможности в таблице. При нулевой нагрузке относительное покрытие не определено. Число необходимых паромов без выработки не рассчитывается.</p>
      </div>
      <div className="panel decision-report"><h3>Подготовить обоснование решения</h3><p className="tiny-note">Отчёт использует это же сравнение. Он содержит допущения, пороги, источники, ограничения и вопросы для практического решения; ответ LLM необязателен.</p><div className="decision-buttons"><button className="button-primary" onClick={() => void exportResult('html')} disabled={exportBusy}><FileText size={16} />Печатная HTML-версия</button><button className="button-secondary" onClick={() => void exportResult('csv')} disabled={exportBusy}>CSV сравнения</button><button className="button-secondary" onClick={() => void exportResult('json')} disabled={exportBusy}>JSON снимка</button><button className="button-secondary" onClick={onOpenAssistant}><Sparkles size={16} />Объяснить сравнение</button></div><p className="tiny-note">В HTML-отчёте доступна кнопка «Печать / Сохранить в PDF», открывающая системный диалог печати.</p></div>
    </div>}
    <div className="decision-profile-controls"><button type="button" className="button-secondary" disabled={profileBusy || region !== 'kaliningrad' || !supported} onClick={() => void loadProfile()}>{profileBusy ? 'Загрузка…' : 'Показать ввоз и вывоз вместе'}</button><p className="tiny-note">Отдельный просмотр профиля не добавляет его задним числом в снимок. Для отчёта включите профиль перед расчётом.</p></div>
    {profileError && <p role="alert" className="decision-error">{profileError}</p>}
    {profileShown && <DirectionView profile={profileShown} factor={factor} onPassport={setPassport} />}
    {passport && <NumberPassport passport={passport} onClose={closePassport} />}
  </section>
}
