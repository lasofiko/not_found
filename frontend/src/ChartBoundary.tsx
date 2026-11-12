import { Component, type ReactNode } from 'react'

export class ChartBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() { return { failed: true } }

  render() {
    return this.state.failed
      ? <div className="chart-canvas chart-loading" role="status">График недоступен. Таблица, выгрузка и калькулятор продолжают работать. Обновите страницу, чтобы повторить загрузку.</div>
      : this.props.children
  }
}
