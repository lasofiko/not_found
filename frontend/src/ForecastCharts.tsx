import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { calculateForecast } from './dashboard-data'

type Forecast = ReturnType<typeof calculateForecast>

const colors = {
  teal: '#32AA9B',
  tealDark: '#278D84',
  violet: '#7967D5',
  violetLight: '#AA99EC',
  blue: '#6C91D4',
}

const getTooltipStyle = (light: boolean) => ({
  background: light ? '#FCFCFE' : '#1B1E32',
  border: light ? '1px solid #E3E4ED' : '1px solid #3A3F58',
  borderRadius: '12px',
  boxShadow: light ? '0 14px 35px rgba(37, 39, 66, 0.13)' : '0 14px 35px rgba(0, 0, 0, 0.35)',
  color: light ? '#22263A' : '#F0F0F7',
  fontSize: '12px',
})

const getAxisStyle = (light: boolean) => ({
  fill: light ? '#747B90' : '#A3A8BC',
  fontSize: 11,
  fontFamily: 'Inter',
})

function ChartLegend({ items }: { items: { name: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="chart-legend" aria-label="Легенда графика">
      {items.map((item) => (
        <span key={item.name}>
          <i className="legend-line" style={{ backgroundColor: item.color, color: item.color }} />
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

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Столбчатый график объёма груза в 2025 и 2030 годах по видам транспорта">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: -22, bottom: 0 }} barCategoryGap="38%">
            <defs>
              <linearGradient id="autoPremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#48BCAB" /><stop offset="100%" stopColor={colors.tealDark} /></linearGradient>
              <linearGradient id="railPremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#91A9DE" /><stop offset="100%" stopColor={colors.blue} /></linearGradient>
              <linearGradient id="seaPremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={colors.violetLight} /><stop offset="100%" stopColor={colors.violet} /></linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} dy={6} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} domain={[0, 'dataMax + 4']} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: light ? 'rgba(112, 95, 208, 0.035)' : 'rgba(163, 149, 241, 0.05)' }} formatter={(value) => [`${value} тыс. т/сутки`]} />
            <Bar dataKey="Авто" stackId="volume" fill="url(#autoPremium)" isAnimationActive={false}><LabelList dataKey="Авто" position="center" fill="#F4FFFC" fontSize={11} fontWeight={650} /></Bar>
            <Bar dataKey="Ж/Д" stackId="volume" fill="url(#railPremium)" isAnimationActive={false}><LabelList dataKey="Ж/Д" position="center" fill="#F8FAFF" fontSize={11} fontWeight={600} /></Bar>
            <Bar dataKey="Море" stackId="volume" fill="url(#seaPremium)" radius={[6, 6, 0, 0]} isAnimationActive={false}><LabelList dataKey="Море" position="center" fill="#FCFAFF" fontSize={11} fontWeight={600} /></Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Авто', color: colors.teal }, { name: 'Ж/Д', color: colors.blue }, { name: 'Море', color: colors.violet }]} />
    </div>
  )
}

export function DynamicsChart({ data, light = false }: { data: Forecast['dynamics']; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#E8E8F0' : '#292D43'

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Линейный график динамики объёма груза с 2025 по 2030 год для трёх сценариев">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 10, left: -22, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} dy={6} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} domain={['dataMin - 1', 'dataMax + 1']} />
            <Tooltip contentStyle={tooltipStyle} formatter={(value, name) => [`${value} тыс. т/сутки`, name]} />
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
  const lines = value === 'Б. Новый маршрут' ? ['Б. Новый', 'маршрут'] : [value]
  return (
    <text x={x} y={y + 12} textAnchor="middle" fill={light ? '#747B90' : '#A3A8BC'} fontSize={11} fontFamily="Inter">
      {lines.map((line, index) => <tspan key={line} x={x} dy={index === 0 ? 0 : 13}>{line}</tspan>)}
    </text>
  )
}

export function HypothesisChart({ data, timeUnit, light = false }: { data: Forecast['hypotheses']; timeUnit: string; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#E8E8F0' : '#292D43'

  return (
    <div className="chart-wrap comparison-chart-wrap">
      <div className="chart-canvas comparison-canvas" role="img" aria-label={`Сравнение трёх гипотез по объёму груза и времени доставки в ${timeUnit}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: -4, left: -20, bottom: 0 }} barCategoryGap="30%">
            <defs><linearGradient id="comparePremium" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#55C2B2" /><stop offset="100%" stopColor="#328F9B" /></linearGradient></defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 5" />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={<HypothesisTick light={light} />} height={38} interval={0} />
            <YAxis yAxisId="volume" axisLine={false} tickLine={false} tick={axisStyle} />
            <YAxis yAxisId="time" orientation="right" axisLine={false} tickLine={false} tick={axisStyle} width={32} />
            <Tooltip contentStyle={tooltipStyle} formatter={(value, name) => name === 'Объём' ? [`${value} тыс. т/сутки`, name] : [`${value} ${timeUnit}`, name]} />
            <Legend content={() => null} />
            <Bar yAxisId="volume" name="Объём" dataKey="volume" fill="url(#comparePremium)" radius={[6, 6, 0, 0]} maxBarSize={42} isAnimationActive={false} />
            <Line yAxisId="time" name="Время" type="monotone" dataKey="time" stroke={colors.violet} strokeWidth={2.3} dot={{ r: 4, fill: light ? '#FCFCFE' : '#15182A', stroke: colors.violet, strokeWidth: 2 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Объём, тыс. т/сутки', color: colors.teal }, { name: `Время, ${timeUnit}`, color: colors.violet }]} />
    </div>
  )
}
