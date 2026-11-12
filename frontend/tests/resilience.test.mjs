import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { useCallback, useState } from 'react'
import { act, browser, component, createElement as h } from './dom-harness.mjs'

const { AssistantDialog } = await component('AssistantDialog')
const charts = await component('ChartPanels', [{ name: 'unavailable-charts', setup(build) {
  build.onLoad({ filter: /ForecastCharts\.tsx$/ }, () => ({ contents: 'throw new Error("Синтетическая ошибка загрузки модуля")', loader: 'js' }))
} }])

test('панель ассистента: dialog, начальный фокус, Tab/Shift+Tab, Escape, фон и восстановление фокуса', async t => {
  const page = browser(); t.after(() => page.close())
  const external = document.createElement('button')
  external.textContent = 'Внешний портал'
  document.body.appendChild(external)
  function Shell() {
    const [open, setOpen] = useState(false)
    const close = useCallback(() => setOpen(false), [])
    return h('div', null,
      h('main', { id: 'background' }, h('button', { id: 'launch', onClick: () => setOpen(true) }, 'Открыть')),
      h(AssistantDialog, { open, onClose: close },
        h('h2', { id: 'assistant-title' }, 'AI-ассистент'),
        h('button', { id: 'close', onClick: close }, 'Закрыть'),
        h('input', { id: 'question' }), h('button', { id: 'last' }, 'Отправить')))
  }
  await page.render(h(Shell))
  const launch = page.container.querySelector('#launch')
  launch.focus()
  await act(async () => launch.click())
  const dialog = page.container.querySelector('[role="dialog"]')
  const close = page.container.querySelector('#close')
  const last = page.container.querySelector('#last')
  assert.equal(dialog.getAttribute('aria-modal'), 'true')
  assert.equal(dialog.getAttribute('aria-labelledby'), 'assistant-title')
  assert.equal(document.activeElement, close)
  assert.equal(page.container.querySelector('#background').hasAttribute('inert'), true)
  assert.equal(external.hasAttribute('inert'), true, 'фон вне React root также недоступен')
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(page.key(close, 'Tab', true), false)
  assert.equal(document.activeElement, last)
  assert.equal(page.key(last, 'Tab'), false)
  assert.equal(document.activeElement, close)
  launch.focus()
  assert.equal(document.activeElement, close, 'фокус не уходит на фон даже при программном фокусе')
  await act(async () => page.key(close, 'Escape'))
  assert.equal(dialog.hidden, true)
  assert.equal(document.activeElement, launch)
  assert.equal(page.container.querySelector('#background').hasAttribute('inert'), false)
  assert.equal(external.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, '')
  await act(async () => launch.click())
  await act(async () => page.container.querySelector('[data-modal-backdrop]').click())
  assert.equal(dialog.hidden, true)
  assert.equal(document.activeElement, launch)
})

test('закрытая панель и существующий inert фона сохраняют предусмотренное состояние', async t => {
  const page = browser(); t.after(() => page.close())
  const close = () => {}
  await page.render(h('div', null, h('main', { inert: true, id: 'existing' }), h(AssistantDialog, { open: true, onClose: close }, h('h2', { id: 'assistant-title' }, 'AI'))))
  const dialog = page.container.querySelector('[role="dialog"]')
  assert.equal(document.activeElement, dialog, 'пустая панель сама принимает фокус')
  await page.render(h('div', null, h('main', { inert: true, id: 'existing' }), h(AssistantDialog, { open: false, onClose: close }, h('h2', { id: 'assistant-title' }, 'AI'))))
  assert.equal(page.container.querySelector('#existing').hasAttribute('inert'), true)
})

test('отказ загрузки каждого ленивого графика остаётся локальным', async t => {
  const page = browser(); t.after(() => page.close())
  const oldConsoleError = console.error
  console.error = () => {}
  t.after(() => { console.error = oldConsoleError })
  await page.render(h('div', null, h('button', { id: 'calculator' }, 'Рассчитать'), h('table', null, h('tbody', null, h('tr', null, h('td', null, 'Данные API')))),
    ...Object.values(charts).map((Chart, index) => h(Chart, { key: index }))))
  await act(async () => { await new Promise(setImmediate) })
  assert.equal(page.container.querySelectorAll('[role="status"]').length, 6)
  for (const message of page.container.querySelectorAll('[role="status"]')) assert.match(message.textContent, /График недоступен/)
  assert.equal(page.container.querySelector('table').textContent, 'Данные API')
  assert.equal(page.container.querySelector('#calculator').disabled, false)
})

test('backdrop ассистента имеет единое видимое правило после мобильных стилей', () => {
  const css = readFileSync(new URL('../src/App.css', import.meta.url), 'utf8')
  const final = [...css.matchAll(/\.assistant-backdrop\s*\{([^}]+)\}/g)].at(-1)[1]
  assert.match(final, /display:\s*block/)
  assert.match(final, /z-index:\s*59/)
})
