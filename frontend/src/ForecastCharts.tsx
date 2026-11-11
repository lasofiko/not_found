import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Forecast } from './api'

type Props = { result: Forecast | null; light?: boolean }
function Empty() { return <p className="chart-empty">Прогноз отсутствует. Запросите проверенный результат через API.</p> }
const color = '#7967D5'
const grid = '#44465c'
export function VolumeChart({ result }: Props) {
  if (!result) return <Empty />
  return <div className="chart-wrap">
    <div className="chart-canvas" role="img" aria-label="Прогноз перевезённой массы, тонн за год">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={result.predictions}>
          <CartesianGrid stroke={grid} strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="period" />
          <YAxis width={75} />
          <Tooltip formatter={value => [Number(value).toLocaleString('ru-RU'), 'т/год']} />
          <Bar name="Прогноз, т/год" dataKey="value" fill={color} radius={[6, 6, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <div className="chart-legend">Прогноз перевезённой массы · т/год</div>
  </div>
}
export function DynamicsChart({ result }: Props) {
  if (!result) return <Empty />
  const rows = result.predictions.map(p => ({ period: p.period, forecast: p.value, lower: p.interval['95'].lower, upper: p.interval['95'].upper }))
  return <div className="chart-wrap">
    <div className="chart-canvas" role="img" aria-label="Динамика прогноза с условными границами 95 процентов">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows}>
          <CartesianGrid stroke={grid} strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="period" />
          <YAxis width={75} />
          <Tooltip formatter={value => [Number(value).toLocaleString('ru-RU'), 'т/год']} />
          <Line name="Прогноз" dataKey="forecast" stroke={color} isAnimationActive={false} />
          <Line name="Нижняя 95%" dataKey="lower" stroke="#32AA9B" strokeDasharray="4 4" dot={false} isAnimationActive={false} />
          <Line name="Верхняя 95%" dataKey="upper" stroke="#6C91D4" strokeDasharray="4 4" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <div className="chart-legend">Прогноз и условный интервал 95% · т/год</div>
  </div>
}
export function HypothesisChart({ result }: Props) {
  if (!result) return <Empty />
  const rows = [{ name: result.selected_model, MAE: result.metrics.selected.holdout.MAE }, { name: 'naive baseline', MAE: result.metrics.baseline.holdout.MAE }]
  return <div className="chart-wrap">
    <div className="chart-canvas" role="img" aria-label="MAE выбранного метода и baseline на проверке 2022–2023">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows}>
          <CartesianGrid stroke={grid} strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="name" />
          <YAxis width={75} />
          <Tooltip formatter={value => [Number(value).toLocaleString('ru-RU'), 'MAE, т']} />
          <Bar name="MAE, т" dataKey="MAE" fill="#32AA9B" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <div className="chart-legend">MAE · т · горизонт проверки один год</div>
  </div>
}
