import assert from 'node:assert/strict'
import test from 'node:test'
import { act, browser, component, createElement as h } from './dom-harness.mjs'
import { syntheticForecast } from './forecast-fixture.mjs'

const { default: App } = await component('App', [{ name: 'isolate-unrelated-presentation', setup(build) {
  build.onLoad({ filter: /(?:ChartPanels|FilterSelect|GeographyFilters|TransportGallery)\.tsx$/ }, args => {
    const module = args.path.split(/[\\/]/).at(-1).replace('.tsx', '')
    const names = module === 'ChartPanels' ? ['DynamicsChart', 'ForecastDynamicsChart', 'ForecastVolumeChart', 'HypothesisChart', 'ModelQualityChart', 'VolumeChart'] : [module]
    return { contents: names.map(name => `export function ${name}() { return null }`).join('\n'), loader: 'js' }
  })
} }])

test('Escape в настоящем App отменяет summary, игнорирует поздний ответ и позволяет повторное открытие', async t => {
  const page = browser(); t.after(() => page.close())
  const summaries = []
  const context = syntheticForecast()
  global.fetch = async (url, options) => {
    if (url === '/config.json') return Response.json({ apiBase: '/api/v1' })
    if (url.includes('/forecasts?')) return Response.json(context)
    if (url.endsWith('/scenarios')) return Response.json([])
    if (url.includes('/llm/summary?')) return new Promise(resolve => summaries.push({ signal: options.signal, reply: answer => resolve(Response.json({ answer, result_type: 'interpretation', context })) }))
    throw new Error('Неожиданный тестовый адрес ' + url)
  }
  await page.render(h(App))
  const launcher = page.container.querySelector('.assistant-launcher')
  launcher.focus()
  await act(async () => launcher.click())
  assert.equal(summaries.length, 1)
  assert.equal(summaries[0].signal.aborted, false)
  const dialog = page.container.querySelector('[role="dialog"]')
  await act(async () => page.key(document.activeElement, 'Escape'))
  assert.equal(dialog.hidden, true)
  assert.equal(summaries[0].signal.aborted, true)
  assert.equal(document.activeElement, launcher)
  await act(async () => launcher.click())
  assert.equal(summaries.length, 2)
  await act(async () => summaries[0].reply('Старый отменённый ответ'))
  assert.doesNotMatch(dialog.textContent, /Старый отменённый ответ/)
  await act(async () => summaries[1].reply('Новая проверяемая интерпретация'))
  assert.match(dialog.textContent, /Новая проверяемая интерпретация/)
})
