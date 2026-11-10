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
import type { selectDemoScenario } from './dashboard-data'

type Forecast = ReturnType<typeof selectDemoScenario>

const getTooltipStyle = (light: boolean) => ({
  background: light ? '#FFFFFF' : '#0A2038',
  border: light ? '1px solid #C8D9E2' : '1px solid #235B70',
  borderRadius: '12px',
  boxShadow: light ? '0 10px 30px rgba(32, 66, 88, 0.14)' : '0 10px 30px rgba(0, 0, 0, 0.45)',
  color: light ? '#17324A' : '#EAF6FF',
  fontSize: '12px',
})

const getAxisStyle = (light: boolean) => ({
  fill: light ? '#526E80' : '#87A6BB',
  fontSize: 12,
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
  const gridColor = light ? '#D7E3EA' : '#17374D'

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Столбчатый график объёма груза в 2025 и 2030 годах для моря, synthetic, тыс. т/сутки">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: -22, bottom: 0 }} barCategoryGap="38%">
            <defs>
              <linearGradient id="autoNeon" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#35F2D5" /><stop offset="100%" stopColor="#149D9C" /></linearGradient>
              <linearGradient id="railNeon" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#57C7FF" /><stop offset="100%" stopColor="#3478E5" /></linearGradient>
              <linearGradient id="seaNeon" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#C379FF" /><stop offset="100%" stopColor="#7945D7" /></linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 4" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} dy={6} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} domain={[0, 'dataMax + 4']} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(45, 230, 224, 0.04)' }} formatter={(value) => [`${value} тыс. т/сутки`]} />
            <Bar dataKey="Море" stackId="volume" fill="url(#seaNeon)" radius={[6, 6, 0, 0]} isAnimationActive={false}><LabelList dataKey="Море" position="center" fill="#F7EEFF" fontSize={11} fontWeight={600} /></Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Море · synthetic, тыс. т/сутки', color: '#AC68F5' }]} />
    </div>
  )
}

export function DynamicsChart({ data, light = false }: { data: Forecast['dynamics']; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#D7E3EA' : '#17374D'

  return (
    <div className="chart-wrap">
      <div className="chart-canvas" role="img" aria-label="Линейный график динамики объёма груза с 2025 по 2030 год для трёх синтетических примеров, тыс. т/сутки">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 10, left: -22, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 4" />
            <XAxis dataKey="year" axisLine={false} tickLine={false} tick={axisStyle} dy={6} />
            <YAxis axisLine={false} tickLine={false} tick={axisStyle} domain={['dataMin - 1', 'dataMax + 1']} />
            <Tooltip contentStyle={tooltipStyle} formatter={(value, name) => [`${value} тыс. т/сутки`, name]} />
            <Line type="monotone" dataKey="Базовый" stroke="#2DE6CF" strokeWidth={2.3} dot={{ r: 3, fill: '#2DE6CF', strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
            <Line type="monotone" dataKey="А. Мощность" stroke="#4AA8FF" strokeWidth={2.1} dot={{ r: 2.5, fill: '#4AA8FF', strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
            <Line type="monotone" dataKey="Б. Новый маршрут" stroke="#B86CFF" strokeWidth={2.1} dot={{ r: 2.5, fill: '#B86CFF', strokeWidth: 0 }} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Базовый', color: '#2DE6CF' }, { name: 'А. Мощность', color: '#4AA8FF' }, { name: 'Б. Новый маршрут', color: '#B86CFF' }]} />
    </div>
  )
}

function HypothesisTick({ x = 0, y = 0, payload, light = false }: { x?: number; y?: number; payload?: { value: string }; light?: boolean }) {
  const value = payload?.value ?? ''
  const lines = value === 'Б. Новый маршрут' ? ['Б. Новый', 'маршрут'] : [value]
  return (
    <text x={x} y={y + 12} textAnchor="middle" fill={light ? '#526E80' : '#87A6BB'} fontSize={11} fontFamily="Inter">
      {lines.map((line, index) => <tspan key={line} x={x} dy={index === 0 ? 0 : 13}>{line}</tspan>)}
    </text>
  )
}

export function HypothesisChart({ data, timeUnit, light = false }: { data: Forecast['hypotheses']; timeUnit: string; light?: boolean }) {
  const tooltipStyle = getTooltipStyle(light)
  const axisStyle = getAxisStyle(light)
  const gridColor = light ? '#D7E3EA' : '#17374D'

  return (
    <div className="chart-wrap comparison-chart-wrap">
      <div className="chart-canvas comparison-canvas" role="img" aria-label={`Сравнение трёх гипотез по объёму груза и времени доставки в ${timeUnit}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: -4, left: -20, bottom: 0 }} barCategoryGap="30%">
            <defs><linearGradient id="compareNeon" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#39F0D4" /><stop offset="100%" stopColor="#2589C8" /></linearGradient></defs>
            <CartesianGrid vertical={false} stroke={gridColor} strokeDasharray="3 4" />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={<HypothesisTick light={light} />} height={38} interval={0} />
            <YAxis yAxisId="volume" axisLine={false} tickLine={false} tick={axisStyle} />
            <YAxis yAxisId="time" orientation="right" axisLine={false} tickLine={false} tick={axisStyle} width={32} />
            <Tooltip contentStyle={tooltipStyle} formatter={(value, name) => name === 'Объём' ? [`${value} тыс. т/сутки`, name] : [`${value} ${timeUnit}`, name]} />
            <Legend content={() => null} />
            <Bar yAxisId="volume" name="Объём" dataKey="volume" fill="url(#compareNeon)" radius={[6, 6, 0, 0]} maxBarSize={42} isAnimationActive={false} />
            <Line yAxisId="time" name="Время" type="monotone" dataKey="time" stroke="#B86CFF" strokeWidth={2.3} dot={{ r: 4, fill: light ? '#FFFFFF' : '#081D33', stroke: '#B86CFF', strokeWidth: 2 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ name: 'Объём, тыс. т/сутки', color: '#2DE6CF' }, { name: `Время, ${timeUnit}`, color: '#B86CFF' }]} />
    </div>
  )
}
