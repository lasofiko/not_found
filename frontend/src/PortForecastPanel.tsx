import { useEffect, useMemo, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { FilterSelect } from './FilterSelect'
import { DISPLAY_LABELS, chartRows, getPortCatalog, getPortForecast, verdictText } from './port-api.ts'
import type { PortCatalog, PortForecast } from './port-api.ts'

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })
const compact = new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 })
const pct = (value: number | null) => value == null ? '—' : `${value.toFixed(1).replace('.', ',')}%`
const END_PERIODS = ['2026-06', '2026-12', '2027-12']

export function PortForecastPanel({ light }: { light: boolean }) {
  const [catalog, setCatalog] = useState<PortCatalog | null>(null)
  const [target, setTarget] = useState('')
  const [endPeriod, setEndPeriod] = useState('2026-12')
  const [loaded, setLoaded] = useState<{ key: string; forecast: PortForecast | null; error: string }>({ key: '', forecast: null, error: '' })
  const [catalogError, setCatalogError] = useState('')
  const [retry, setRetry] = useState(0)
  const key = `${target}|${endPeriod}|${retry}`
  const loading = Boolean(target) && loaded.key !== key
  const forecast = loaded.key === key ? loaded.forecast : null
  const error = catalogError || (loaded.key === key ? loaded.error : '')

  useEffect(() => {
    const controller = new AbortController()
    getPortCatalog(controller.signal).then(result => {
      setCatalog(result)
      setTarget(current => current || result.primary_target)
    }).catch(cause => { if (cause?.name !== 'AbortError') setCatalogError(cause instanceof Error ? cause.message : String(cause)) })
    return () => controller.abort()
  }, [retry])

  useEffect(() => {
    if (!target) return
    const controller = new AbortController()
    const requestKey = `${target}|${endPeriod}|${retry}`
    getPortForecast(target, endPeriod, controller.signal)
      .then(result => setLoaded({ key: requestKey, forecast: result, error: '' }))
      .catch(cause => {
        if (cause?.name === 'AbortError') return
        setLoaded({ key: requestKey, forecast: null, error: cause instanceof Error ? cause.message : String(cause) })
      })
    return () => controller.abort()
  }, [target, endPeriod, retry])

  const summary = catalog?.targets.find(t => t.target_id === target)
  const rows = useMemo(() => forecast ? chartRows(forecast) : [], [forecast])
  const colors = light ? { history: '#626A80', forecast: '#4C70B4', band: '#4C70B4', grid: '#E8E8F0', axis: '#626A80' }
    : { history: '#A3A8BC', forecast: '#89A9E0', band: '#89A9E0', grid: '#292D43', axis: '#A3A8BC' }
  const next = forecast?.predictions[0]
  const sourceIds = forecast ? [...new Set([
    ...(forecast.provenance.source_ids ?? []),
    ...(forecast.provenance.sources ?? []).map(source => source.source_id),
  ])] : []

  return (
    <section className="section" id="port-forecast" aria-labelledby="port-forecast-title">
      <div className="section-heading"><div><p className="eyebrow">Месячный прогноз</p><h2 id="port-forecast-title">Перевалка в каботаже по портам.</h2><p>Тонны за месяц, история до декабря 2025. Методы выбраны по окну июль 2024 — июнь 2025 и проверены на 2026 году. Для паромов и всего каботажа выбор пересмотрен после проверки — это указано в карточке цели.</p></div><span className="section-number">ПОРТЫ</span></div>
      <div className="filterbar">
        <div className="filters geography-actions">
          <FilterSelect label="Порт и группа грузов" value={target} disabled={!catalog} onChange={setTarget}
            options={(catalog?.targets ?? []).map(t => ({ value: t.target_id, label: `${t.role}: ${t.title}` }))} />
          <FilterSelect label="Прогноз до" value={endPeriod} onChange={setEndPeriod}
            options={END_PERIODS.map(p => ({ value: p, label: p === '2027-12' ? '2027-12 (экспериментально)' : p }))} />
          <button className="button-secondary" onClick={() => { setCatalogError(''); setRetry(v => v + 1) }} disabled={loading}><RotateCcw size={16} />Обновить</button>
        </div>
        <p className="filter-status" role="status" aria-live="polite" aria-busy={loading}>{loading ? 'Загрузка прогноза…' : error || (forecast ? `Модель ${forecast.model_version}, метод ${forecast.selected_model}` : '')}</p>
      </div>
      {summary && <div className="forecast-banner"><div><span className="tag">{DISPLAY_LABELS[summary.display]}</span><h3>{summary.title}</h3><p>{summary.note}</p></div></div>}
      {summary && <div className="kpi-grid">
        <article className="kpi-card"><div className="kpi-top"><span>{next ? `Прогноз на ${next.period}` : 'Прогноз'}</span></div><div className="kpi-value">{next ? number.format(next.value) : '—'}<small>т/мес</small></div><div className="kpi-bottom">{next ? `80%: ${compact.format(next.interval['80'].lower)}–${compact.format(next.interval['80'].upper)} т` : summary.display === 'history_only' ? 'Прогноз не публикуется' : 'Модель не подключена'}</div></article>
        <article className="kpi-card"><div className="kpi-top"><span>Проверка {summary.quality.holdout.period}</span></div><div className="kpi-value">{pct(summary.quality.holdout.selected_WAPE)}</div><div className="kpi-bottom">WAPE на месяц вперёд · naive {pct(summary.quality.holdout.naive_WAPE)}</div></article>
        <article className="kpi-card"><div className="kpi-top"><span>2026, январь–август</span></div><div className="kpi-value">{pct(summary.quality.blind_2026[0].selected_APE)}</div><div className="kpi-bottom">ошибка суммы · naive {pct(summary.quality.blind_2026[0].naive_APE)}</div></article>
        <article className="kpi-card"><div className="kpi-top"><span>2026, сентябрь</span></div><div className="kpi-value">{pct(summary.quality.blind_2026[1].selected_APE)}</div><div className="kpi-bottom">{verdictText(summary.quality.blind_verdict)}</div></article>
      </div>}
      <article className="panel"><div className="panel-top"><div><h3>История и прогноз</h3><p>Тонны за месяц · полоса — условный интервал 80%</p></div><span className="tag">{forecast ? DISPLAY_LABELS[forecast.display] : 'НЕТ ДАННЫХ'}</span></div>
        {forecast ? <div className="chart-wrap"><div className="chart-canvas" role="img" aria-label={`${forecast.title}: история и прогноз, тонны за месяц`}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid vertical={false} stroke={colors.grid} strokeDasharray="3 5" />
              <XAxis dataKey="period" axisLine={false} tickLine={false} tick={{ fill: colors.axis, fontSize: 11 }} minTickGap={24} />
              <YAxis axisLine={false} tickLine={false} tick={{ fill: colors.axis, fontSize: 11 }} width={72} tickFormatter={v => compact.format(v)} domain={[0, 'auto']} />
              <Tooltip formatter={(value, name) => [Array.isArray(value) ? value.map(v => number.format(Number(v))).join(' – ') + ' т' : number.format(Number(value)) + ' т', name === 'band' ? 'интервал 80%' : name === 'history' ? 'история · расчётная оценка' : 'прогноз']} />
              <Area dataKey="band" stroke="none" fill={colors.band} fillOpacity={0.15} isAnimationActive={false} />
              <Line dataKey="history" stroke={colors.history} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line dataKey="forecast" stroke={colors.forecast} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer></div></div> : <p className="tiny-note">{error || 'Выберите цель.'}</p>}
        {forecast && <ul className="tiny-note">{forecast.warnings.map(w => <li key={w}>{w}</li>)}</ul>}
        {forecast && <details className="tiny-note"><summary>Происхождение и период расчёта</summary>
          <p>История — расчётные месячные разности (estimated), {forecast.training_period.start} — {forecast.training_period.end}. Направления и OD не выделены.</p>
          <p>Версия данных: {forecast.data_version}. Данные доступны к {forecast.training_data_available_by}. Артефакт выпущен {forecast.generated_at}.</p>
          <p>Источники: {sourceIds.length ? sourceIds.join(', ') : 'Идентификаторы не перечислены в артефакте.'}</p>
        </details>}
      </article>
    </section>
  )
}
