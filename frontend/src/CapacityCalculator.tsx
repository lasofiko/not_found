import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { calculateCapacity, getApiBase } from './api'
import type { CapacityInput, CapacityResult } from './api'
import './CapacityCalculator.css'

const fields = [
  ['demand_tonnes', 'Потребность, т за период'],
  ['capacity_tonnes', 'Доступная мощность, т за период'],
  ['capacity_delta_tonnes', 'Дополнительная мощность, т за период'],
] as const
const format = (value: number) => value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })

export function CapacityCalculator() {
  const [result, setResult] = useState<CapacityResult | null>(null)
  const [message, setMessage] = useState('Введите значения для одного периода. Расчёт не является прогнозом.')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const request = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  useEffect(() => () => { request.current?.abort() }, [])

  const invalidate = () => {
    sequence.current += 1
    request.current?.abort()
    setResult(null)
    setError('')
    setLoading(false)
    setMessage('Параметры изменены. Выполните расчёт.')
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    request.current?.abort()
    const id = ++sequence.current
    const controller = new AbortController()
    request.current = controller
    const form = new FormData(event.currentTarget)
    const input: CapacityInput = {
      period: String(form.get('period')),
      demand_tonnes: Number(form.get('demand_tonnes')),
      capacity_tonnes: Number(form.get('capacity_tonnes')),
      capacity_delta_tonnes: Number(form.get('capacity_delta_tonnes')),
      assumption_source: String(form.get('assumption_source')).trim(),
    }
    setResult(null)
    setError('')
    setLoading(true)
    setMessage('Расчёт…')
    try {
      if (!input.assumption_source) throw new Error('Укажите источник допущения.')
      const base = await getApiBase(controller.signal)
      const response = await calculateCapacity(base, input, controller.signal)
      if (sequence.current === id) { setResult(response); setMessage('Сценарный расчёт выполнен.') }
    } catch (cause) {
      if (sequence.current === id && !controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : 'Ошибка расчёта. Повторите запрос.')
        setMessage('')
      }
    } finally {
      if (sequence.current === id) setLoading(false)
    }
  }

  return <section className="info-card capacity-calculator" id="capacity" aria-labelledby="capacity-title">
    <div className="panel-heading">
      <SlidersHorizontal size={18} />
      <div><h2 id="capacity-title">Калькулятор мощности</h2><p>Сценарий по вводу пользователя · т за указанный период</p></div>
    </div>
    <p>Задайте потребность, доступную мощность и её прирост. Все значения относятся к одному году или месяцу. Модель для этого расчёта не требуется.</p>
    <form className="capacity-form" onSubmit={submit} onInput={invalidate}>
      <label>Период
        <input name="period" placeholder="2027 или 2027-01" pattern="[0-9]{4}(-[0-9]{2})?" title="Год YYYY или месяц YYYY-MM" required />
      </label>
      {fields.map(([name, label]) => <label key={name}>{label}<input name={name} type="number" min="0" step="any" required /></label>)}
      <label className="capacity-source">Источник допущения
        <input name="assumption_source" placeholder="Ввод пользователя / документ с датой" maxLength={2000} required />
      </label>
      <button className="button-primary" type="submit" disabled={loading}>{loading ? 'Расчёт…' : 'Рассчитать мощность'}</button>
    </form>
    <div className="filter-status" role="status" aria-live="polite" aria-busy={loading}>{message}</div>
    {error && <p role="alert">{error}</p>}
    {result && <div className="capacity-result" data-testid="capacity-result">
      <h3>Результат за {result.period}</h3>
      <dl>
        <div><dt>Дефицит до действия</dt><dd>{format(result.baseline_shortfall_tonnes)} т</dd></div>
        <div><dt>Мощность после действия</dt><dd>{format(result.scenario_capacity_tonnes)} т</dd></div>
        <div><dt>Дефицит после действия</dt><dd>{format(result.scenario_shortfall_tonnes)} т</dd></div>
        <div><dt>Сокращение дефицита</dt><dd>{format(result.shortfall_reduction_tonnes)} т</dd></div>
        <div><dt>Запас мощности</dt><dd>{format(result.scenario_spare_capacity_tonnes)} т</dd></div>
      </dl>
      <p>Статус: сценарий. Единица: тонны за {result.period}. Источник: {result.assumption_source}</p>
      <p>{result.warning}</p>
    </div>}
  </section>
}
