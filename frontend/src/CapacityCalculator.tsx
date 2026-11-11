import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight } from 'lucide-react'
import { calculateCapacity, type CapacityResult } from './api'
import { formatNumber } from './dashboard-data'
import './CapacityCalculator.css'

export function CapacityCalculator() {
  const [result, setResult] = useState<CapacityResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('Введите собственные допущения за один и тот же период.')
  const pending = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  useEffect(() => () => { pending.current?.abort() }, [])

  const invalidate = () => {
    sequence.current += 1
    pending.current?.abort()
    pending.current = null
    setResult(null)
    setLoading(false)
    setError('')
    setMessage('Входные данные изменены. Выполните расчёт заново.')
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending.current) return
    const data = new FormData(event.currentTarget)
    const controller = new AbortController()
    pending.current = controller
    const version = ++sequence.current
    setLoading(true)
    setResult(null)
    setError('')
    setMessage('Расчёт через FastAPI…')
    try {
      const response = await calculateCapacity({
        period: String(data.get('period')),
        demand_tonnes: Number(data.get('demand_tonnes')),
        capacity_tonnes: Number(data.get('capacity_tonnes')),
        capacity_delta_tonnes: Number(data.get('capacity_delta_tonnes')),
        assumption_source: String(data.get('assumption_source')).trim(),
      }, controller.signal)
      if (controller.signal.aborted || version !== sequence.current) return
      setResult(response)
      setMessage('Расчёт выполнен по введённым допущениям.')
    } catch (cause) {
      if (controller.signal.aborted || version !== sequence.current) return
      setError(cause instanceof Error ? cause.message : 'Не удалось выполнить расчёт.')
      setMessage('Проверьте параметры и повторите запрос.')
    } finally {
      if (version === sequence.current) {
        pending.current = null
        setLoading(false)
      }
    }
  }

  return <section className="section capacity-section" id="capacity" aria-labelledby="capacity-title">
    <div className="section-heading"><div><p className="eyebrow">Сценарный калькулятор</p><h2 id="capacity-title">Баланс провозной мощности.</h2><p>Чувствительность к вашему вводу. Это сценарный расчёт, а не ML-прогноз.</p></div></div>
    <form className="panel capacity-form" id="capacity-form" onSubmit={submit} onInput={invalidate}>
      <div className="capacity-fields">
        <label className="field"><span>Период</span><input name="period" placeholder="2027 или 2027-01" pattern="(?!0000)[0-9]{4}(-(0[1-9]|1[0-2]))?" title="Год YYYY или месяц YYYY-MM" required /></label>
        <label className="field"><span>Потребность, т за период</span><input name="demand_tonnes" type="number" min="0" step="any" required /></label>
        <label className="field"><span>Доступная мощность, т за период</span><input name="capacity_tonnes" type="number" min="0" step="any" required /></label>
        <label className="field"><span>Дополнительная мощность, т за период</span><input name="capacity_delta_tonnes" type="number" min="0" step="any" required /></label>
        <label className="field capacity-source"><span>Источник допущения</span><input name="assumption_source" placeholder="Источник или описание ваших допущений" maxLength={2000} pattern=".*\S.*" required /></label>
      </div>
      <button className="button-primary" type="submit" disabled={loading}>{loading ? 'Рассчитываем…' : 'Рассчитать'}<ArrowRight size={16} /></button>
      <p className="filter-status" role="status" aria-busy={loading}>{message}</p>
      {error && <p className="capacity-error" role="alert">{error}</p>}
    </form>
    {result && <div className="panel capacity-result" id="capacity-result" aria-live="polite">
      <div className="panel-top"><h3>Результат за {result.period}</h3><span className="tag">СЦЕНАРИЙ · т за период</span></div>
      <dl className="capacity-metrics">
        {([
          ['Дефицит до действия', result.baseline_shortfall_tonnes],
          ['Мощность после действия', result.scenario_capacity_tonnes],
          ['Дефицит после действия', result.scenario_shortfall_tonnes],
          ['Сокращение дефицита', result.shortfall_reduction_tonnes],
          ['Запас мощности', result.scenario_spare_capacity_tonnes],
        ] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(value)} <small>т</small></dd></div>)}
      </dl>
      <p className="tiny-note">{result.warning}</p><p className="tiny-note">Источник: {result.assumption_source}</p>
    </div>}
  </section>
}
