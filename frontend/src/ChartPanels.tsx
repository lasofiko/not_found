import { lazy, Suspense, type ComponentProps } from 'react'
import type * as Charts from './ForecastCharts'
import { ChartBoundary } from './ChartBoundary'

const Dynamics = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.DynamicsChart })))
const Volume = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.VolumeChart })))
const Hypothesis = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.HypothesisChart })))
const fallback = <div className="chart-canvas chart-loading" role="status">Загружаем график…</div>

export function DynamicsChart(props: ComponentProps<typeof Charts.DynamicsChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><Dynamics {...props} /></Suspense></ChartBoundary>
}

export function VolumeChart(props: ComponentProps<typeof Charts.VolumeChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><Volume {...props} /></Suspense></ChartBoundary>
}

export function HypothesisChart(props: ComponentProps<typeof Charts.HypothesisChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><Hypothesis {...props} /></Suspense></ChartBoundary>
}

const ForecastDynamics = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.ForecastDynamicsChart })))
const ForecastVolume = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.ForecastVolumeChart })))
const ModelQuality = lazy(() => import('./ForecastCharts').then((module) => ({ default: module.ModelQualityChart })))

export function ForecastDynamicsChart(props: ComponentProps<typeof Charts.ForecastDynamicsChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><ForecastDynamics {...props} /></Suspense></ChartBoundary>
}

export function ForecastVolumeChart(props: ComponentProps<typeof Charts.ForecastVolumeChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><ForecastVolume {...props} /></Suspense></ChartBoundary>
}

export function ModelQualityChart(props: ComponentProps<typeof Charts.ModelQualityChart>) {
  return <ChartBoundary><Suspense fallback={fallback}><ModelQuality {...props} /></Suspense></ChartBoundary>
}
