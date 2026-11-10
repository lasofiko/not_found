import { lazy, Suspense, type ComponentProps } from 'react'
import type * as Charts from './ForecastCharts'

const Dynamics = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.DynamicsChart })))
const Volume = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.VolumeChart })))
const Hypothesis = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.HypothesisChart })))
const fallback = <div className="chart-canvas chart-loading" role="status">Загружаем график…</div>

export function DynamicsChart(props: ComponentProps<typeof Charts.DynamicsChart>) {
  return <Suspense fallback={fallback}><Dynamics {...props} /></Suspense>
}

export function VolumeChart(props: ComponentProps<typeof Charts.VolumeChart>) {
  return <Suspense fallback={fallback}><Volume {...props} /></Suspense>
}

export function HypothesisChart(props: ComponentProps<typeof Charts.HypothesisChart>) {
  return <Suspense fallback={fallback}><Hypothesis {...props} /></Suspense>
}
