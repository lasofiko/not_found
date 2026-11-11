import type { CSSProperties } from 'react'
import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { selectDemoScenario } from './dashboard-data'

type Forecast = ReturnType<typeof selectDemoScenario>

const darkColors = {
  teal: '#52C5B9',
  violet: '#A395F1',
  blue: '#89A9E0',
}

const lightColors = {
  teal: '#207D75',
  violet: '#705FD0',
  blue: '#4C70B4',
}

const getColors = (light: boolean) => light ? lightColors : darkColors

const getTooltipStyle = (light: boolean): CSSProperties => ({
  background: light ? '#FCFCFE' : '#1B1E32',
  border: light ? '1px solid #E3E4ED' : '1px solid #3A3F58',
  borderRadius: '12px',
  boxShadow: light ? '0 14px 35px rgba(37, 39, 66, 0.13)' : '0 14px 35px rgba(0, 0, 0, 0.35)',
  color: light ? '#22263A' : '#F0F0F7',
  fontSize: '12px',
  padding: '12px 14px',
  lineHeight: 1.6,
  maxWidth: 'min(280px, calc(100vw - 64px))',
  whiteSpace: 'normal',
})

const getAxisStyle = (light: boolean) => ({
  fill: light ? '#626A80' : '#A3A8BC',
  fontSize: 11,
  fontFamily: 'Inter, sans-serif',
})

const axisFormatter = new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 })
const tooltipFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const formatAxisValue = (value: number) => axisFormatter.format(value)
const formatTooltipValue = (value: unknown) => typeof value === 'number' ? tooltipFormatter.format(value) : String(value ?? '—')
const chartMargin = { top: 12, right: 12, left: 0, bottom: 4 }
const getBarCursor = (light: boolean) => ({ fill: light ? 'rgba(112, 95, 208, 0.05)' : 'rgba(163, 149, 241, 0.08)' })

function ChartLegend({ items }: { items: { name: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="chart-legend" aria-label="Легенда графика">
      {items.map((item) => (
        <span key={item.name}>
          <i className={`legend-line${item.dashed ? ' is-dashed' : ''}`} style={{ backgroundColor: item.color, color: item.color }} />
          {item.name}
        </span>
      ))}
    </div>
  )
}

export function VolumeChart({ data, light = false }: { data: Forecast['stacked']; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#E8E8F0' : '#292D43'
  const colors = getColors(light)

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Столбчатый график объёма груза в 2025 и 2030 годах для моря, synthetic, тыс. т/сутки">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={chartMargin} barCategoryGap="38%">
            <defs>
              <linearGradient id="seaPremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={colors.violet} stopOpacity={0.8} /><stop offset="100%" stopColor={colors.violet} /></linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} tickMargin={10} height={32} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} width={42} tickCount={5} domain={[0, 'dataMax + 4']} />
            <Tooltip contentStyle={tooltipStyle} cursor={getBarCursor(light)} formatter={(value) => [`${formatTooltipValue(value)} тыс. т/сутки`]} />
            <Bar dataKey="Море" stackId="volume" fill="url(#seaPremium)" radius={[6, 6, 0, 0]} maxBarSize={64} isAnimationActive={false}><LabelList dataKey="Море" position="center" fill={light ? '#FCFCFF' : '#17132D'} fontSize={11} fontWeight={600} /></Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Море · synthetic, тыс. т/сутки', color: colors.violet }]} />
    </div>
  )
}

export function DynamicsChart({ data, light = false }: { data: Forecast['dynamics']; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#E8E8F0' : '#292D43'
  const colors = getColors(light)

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Линейный график динамики объёма груза с 2025 по 2030 год для трёх синтетических примеров, тыс. т/сутки">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={chartMargin}>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} tickMargin={10} height={32} minTickGap={14} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} width={42} tickCount={5} domain={['dataMin - 1', 'dataMax + 1']} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: gridColor, strokeDasharray: '3 5' }} formatter={(value, name) => [`${formatTooltipValue(value)} тыс. т/сутки`, name]} />
            <Line type="monotone" dataKey="Базовый" stroke={colors.teal} strokeWidth={2.2} dot={{ r: 3, fill: colors.teal, strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
            <Line type="monotone" dataKey="А. Мощность" stroke={colors.blue} strokeWidth={2.1} dot={{ r: 2.5, fill: colors.blue, strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
            <Line type="monotone" dataKey="Б. Новый маршрут" stroke={colors.violet} strokeWidth={2.2} dot={{ r: 2.5, fill: colors.violet, strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Базовый', color: colors.teal }, { name: 'А. Мощность', color: colors.blue }, { name: 'Б. Новый маршрут', color: colors.violet }]} />
    </div>
  )
}

function HypothesisTick({ x = 0, y = 0, payload, light = false }: { x?: number; y?: number; payload?: { value: string }; light?: boolean }) {
  const value = payload?.value ?? ''
  const lines = value === 'Б. Новый маршрут'
    ? ['Б.', 'Маршрут']
    : value === 'А. Мощность'
      ? ['А.', 'Мощность']
      : [value]
  return (
    <text x={x} y={y + 12} textAnchor="middle" fill={light ? '#626A80' : '#A3A8BC'} fontSize={10} fontFamily="Inter">
      {lines.map((line, index) => <tspan key={line} x={x} dy={index === 0 ? 0 : 13}>{line}</tspan>)}
    </text>
  )
}

export function HypothesisChart({ data, timeUnit, light = false }: { data: Forecast['hypotheses']; timeUnit: string; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#E8E8F0' : '#292D43'
  const colors = getColors(light)

  return (
    <div className="chart-wrap comparison-chart-wrap">
      <div className="chart-axis-labels"><span>Объём · тыс. т/сутки</span><span>Время · {timeUnit}</span></div>
      <div className="chart-canvas comparison-canvas" role="img" aria-label={`Сравнение трёх гипотез по объёму груза и времени доставки в ${timeUnit}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 12, right: 0, left: 0, bottom: 4 }} barCategoryGap="30%">
            <defs><linearGradient id="comparePremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={colors.teal} stopOpacity={0.8} /><stop offset="100%" stopColor={colors.teal} /></linearGradient></defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={<HypothesisTick light={light} />} height={38} interval={0} />
            <YAxis
              yAxisId="volume"
              axisLine={false}
              tickLine={false}
              tick={axisStyle}
              width={40}
              tickCount={5}
            />
            <YAxis
              yAxisId="time"
              orientation="right"
              axisLine={false}
              tickLine={false}
              tick={axisStyle}
              width={36}
              tickCount={5}
            />
            <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: gridColor, strokeDasharray: '3 5' }} formatter={(value, name) => name === 'Объём' ? [`${formatTooltipValue(value)} тыс. т/сутки`, name] : [`${formatTooltipValue(value)} ${timeUnit}`, name]} />
            <Bar yAxisId="volume" name="Объём" dataKey="volume" fill="url(#comparePremium)" radius={[6, 6, 0, 0]} maxBarSize={42} isAnimationActive={false} />
            <Line yAxisId="time" name="Время" type="monotone" dataKey="time" stroke={colors.violet} strokeWidth={2.3} dot={{ r: 4, fill: light ? '#FCFCFE' : '#15182A', stroke: colors.violet, strokeWidth: 2 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Объём, тыс. т/сутки', color: colors.teal }, { name: `Время, ${timeUnit}`, color: colors.violet }]} />
    </div>
  )
}


type MlForecast = import('./api').Forecast
type MlChartProps = { result: MlForecast | null; light?: boolean }

function ForecastEmpty() {
  return <p className="chart-empty">Прогноз отсутствует. Запросите проверенный результат через API.</p>
}

export function ForecastVolumeChart({ result, light = false }: MlChartProps) {
  if (!result) return <ForecastEmpty />
  const colors = getColors(light)
  return <div className="chart-wrap">
    <div className="chart-canvas" role="img" aria-label="Прогноз перевезённой массы, тонн за год">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={result.predictions} margin={chartMargin}>
          <CartesianGrid stroke={light ? '#E8E8F0' : '#292D43'} strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="period" axisLine={false} tickLine={false} tick={getAxisStyle(light)} tickMargin={10} height={32} minTickGap={14} />
          <YAxis width={52} tick={getAxisStyle(light)} axisLine={false} tickLine={false} tickFormatter={formatAxisValue} tickCount={5} />
          <Tooltip contentStyle={getTooltipStyle(light)} cursor={getBarCursor(light)} formatter={value => [`${formatTooltipValue(value)} т/год`, 'Прогноз']} />
          <Bar name="Прогноз, т/год" dataKey="value" fill={colors.violet} radius={[6, 6, 0, 0]} maxBarSize={44} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <ChartLegend items={[{ name: 'Прогноз перевезённой массы · т/год', color: colors.violet }]} />
  </div>
}

export function ForecastDynamicsChart({ result, light = false }: MlChartProps) {
  if (!result) return <ForecastEmpty />
  const colors = getColors(light)
  const rows = result.predictions.map(point => ({
    period: point.period,
    forecast: point.value,
    lower: point.interval['95'].lower,
    upper: point.interval['95'].upper,
  }))
  return <div className="chart-wrap">
    <div className="chart-canvas" role="img" aria-label="Динамика прогноза с условными границами 95 процентов">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={chartMargin}>
          <CartesianGrid stroke={light ? '#E8E8F0' : '#292D43'} strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="period" axisLine={false} tickLine={false} tick={getAxisStyle(light)} tickMargin={10} height={32} minTickGap={14} />
          <YAxis width={52} tick={getAxisStyle(light)} axisLine={false} tickLine={false} tickFormatter={formatAxisValue} tickCount={5} />
          <Tooltip contentStyle={getTooltipStyle(light)} cursor={{ stroke: light ? '#E3E4ED' : '#3A3F58', strokeDasharray: '3 5' }} formatter={(value, name) => [`${formatTooltipValue(value)} т/год`, name]} />
          <Line name="Прогноз" dataKey="forecast" stroke={colors.violet} strokeWidth={2.3} dot={{ r: 3, fill: colors.violet, strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
          <Line name="Нижняя 95%" dataKey="lower" stroke={colors.teal} strokeWidth={1.8} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
          <Line name="Верхняя 95%" dataKey="upper" stroke={colors.blue} strokeWidth={1.8} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <ChartLegend items={[{ name: 'Прогноз · т/год', color: colors.violet }, { name: 'Нижняя 95%', color: colors.teal, dashed: true }, { name: 'Верхняя 95%', color: colors.blue, dashed: true }]} />
  </div>
}

export function ModelQualityChart({ result, light = false }: MlChartProps) {
  if (!result) return <ForecastEmpty />
  const colors = getColors(light)
  const rows = [
    { name: result.selected_model, MAE: result.metrics.selected.holdout.MAE },
    { name: 'naive baseline', MAE: result.metrics.baseline.holdout.MAE },
  ]
  return <div className="chart-wrap">
    <div className="chart-canvas quality-canvas" role="img" aria-label="MAE выбранного метода и baseline на проверке 2022–2023">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={chartMargin}>
          <CartesianGrid stroke={light ? '#E8E8F0' : '#292D43'} strokeDasharray="3 5" horizontal={false} />
          <XAxis type="number" tick={getAxisStyle(light)} axisLine={false} tickLine={false} tickFormatter={formatAxisValue} tickMargin={10} height={32} minTickGap={20} />
          <YAxis type="category" dataKey="name" width={104} tick={{ ...getAxisStyle(light), fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={getTooltipStyle(light)} cursor={getBarCursor(light)} formatter={value => [`${formatTooltipValue(value)} т`, 'MAE']} />
          <Bar name="MAE, т" dataKey="MAE" radius={[0, 6, 6, 0]} maxBarSize={30} isAnimationActive={false}>
            {rows.map((row, index) => <Cell key={`${index}-${row.name}`} fill={index === 0 ? colors.violet : colors.teal} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
    <ChartLegend items={[{ name: 'Выбранный метод · MAE, т', color: colors.violet }, { name: 'Naive baseline · MAE, т', color: colors.teal }]} />
  </div>
}
