import assert from 'node:assert/strict'
import test from 'node:test'
import { act, browser, component, createElement } from './dom-harness.mjs'

const { CapacityCalculator } = await component('CapacityCalculator')
const responseData = (overrides = {}) => ({ status: 'scenario', unit: 'tonnes', period: '2027-01',
  baseline_shortfall_tonnes: 20, scenario_capacity_tonnes: 110, scenario_shortfall_tonnes: 0,
  shortfall_reduction_tonnes: 20, scenario_spare_capacity_tonnes: 10,
  warning: 'Расчёт по введённым допущениям; не прогноз.', assumption_source: 'Ввод пользователя', ...overrides })

async function openCalculator(t, { ignoreAbort = false, config = true } = {}) {
  const page = browser()
  t.after(() => page.close())
  const requests = []
  global.fetch = (url, options) => {
    if (url === '/config.json' && config) return Promise.resolve(Response.json({ apiBase: '/api/v1' }))
    return new Promise((resolve, reject) => {
      requests.push({ url, signal: options.signal, body: options.body ? JSON.parse(options.body) : null,
        reply: data => resolve(Response.json(data)), fail: reject })
      if (!ignoreAbort) options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })
  }
  await page.render(createElement(CapacityCalculator))
  const form = page.container.querySelector('form')
  const input = name => form.elements.namedItem(name)
  const edit = async changes => act(async () => {
    for (const [name, value] of Object.entries(changes)) { input(name).value = value; page.event(input(name), 'input') }
  })
  await edit({ period: '2027-01', demand_tonnes: '100', capacity_tonnes: '80', capacity_delta_tonnes: '30', assumption_source: 'Ввод пользователя' })
  return { ...page, requests, form, input, edit, button: form.querySelector('button'),
    submit: () => act(async () => { page.event(form, 'submit') }),
    reply: (index, value = responseData()) => act(async () => { requests[index].reply(value) }),
    output: () => page.container.querySelector('#capacity-result'),
  }
}

test('действующий React: календарный период, неотрицательные числа и источник проверяются до отправки', async t => {
  const page = await openCalculator(t)
  for (const period of ['', '0000', '2027-00', '2027-13', '2027-1', '27', '2027-01-01', '2027–2028']) {
    await page.edit({ period }); await page.submit()
    assert.equal(page.requests.length, 0, period)
  }
  await page.edit({ period: '2027', demand_tonnes: '-1' }); await page.submit()
  await page.edit({ demand_tonnes: '', assumption_source: '  ' }); await page.submit()
  assert.equal(page.requests.length, 0)
  await page.edit({ demand_tonnes: '100', assumption_source: 'Ввод пользователя' }); await page.submit()
  assert.equal(page.requests[0].body.period, '2027')
  assert.equal(page.requests[0].body.demand_tonnes, 100)
  await page.reply(0, responseData({ period: '2027' }))
  await page.edit({ period: '0001-01' }); await page.submit()
  assert.equal(page.requests[1].body.period, '0001-01')
  await page.reply(1, responseData({ period: '0001-01' }))
})

test('React блокирует повторную отправку и выводит все пять результатов и происхождение как текст', async t => {
  const page = await openCalculator(t)
  await page.submit()
  assert.equal(page.button.disabled, true)
  await page.submit()
  assert.equal(page.requests.length, 1)
  const source = '<svg onload=alert(1)>& источник'
  await page.edit({ assumption_source: source })
  assert.equal(page.requests[0].signal.aborted, true)
  await page.submit()
  await page.reply(1, responseData({ assumption_source: source, warning: '<script>alert(2)</script> не прогноз' }))
  assert.equal(page.button.disabled, false)
  assert.equal(page.output().querySelectorAll('dl > div').length, 5)
  assert.deepEqual([...page.output().querySelectorAll('dd')].map(node => node.textContent.trim()), ['20 т', '110 т', '0 т', '20 т', '10 т'])
  for (const label of ['Дефицит до действия', 'Мощность после действия', 'Дефицит после действия', 'Сокращение дефицита', 'Запас мощности']) assert.ok(page.output().textContent.includes(label))
  assert.ok(page.output().textContent.includes(source))
  assert.ok(page.output().textContent.includes('<script>alert(2)</script>'))
  assert.equal(page.output().querySelector('script, svg, img'), null)
})

test('React отменяет запрос при вводе, очищает результат и игнорирует запоздалый ответ', async t => {
  const page = await openCalculator(t, { ignoreAbort: true })
  await page.submit(); await page.edit({ period: '2027-02' })
  assert.equal(page.requests[0].signal.aborted, true)
  assert.equal(page.button.disabled, false)
  assert.equal(page.output(), null)
  await page.submit(); await page.reply(1, responseData({ period: '2027-02' }))
  const expected = page.output().innerHTML
  await page.reply(0)
  assert.equal(page.output().innerHTML, expected)
  assert.match(page.output().textContent, /2027-02/)
  await page.edit({ capacity_delta_tonnes: '40' })
  assert.equal(page.output(), null)
  assert.match(page.container.textContent, /изменены/)
})

test('React после таймаута API позволяет повторить расчёт', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const page = await openCalculator(t)
  await page.submit()
  await act(async () => t.mock.timers.tick(15000))
  assert.match(page.container.querySelector('[role="alert"]').textContent, /ожидания/)
  assert.equal(page.button.disabled, false)
  assert.equal(page.output(), null)
  await page.submit(); await page.reply(1)
  assert.match(page.output().textContent, /2027-01/)
})

test('React ограничивает ожидание конфигурации и отменяет запрос при размонтировании', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const page = await openCalculator(t, { config: false })
  await page.submit()
  assert.equal(page.requests[0].url, '/config.json')
  await act(async () => t.mock.timers.tick(10000))
  assert.match(page.container.querySelector('[role="alert"]').textContent, /ожидания/)
  await page.submit(); await act(async () => page.root.render(null))
  assert.equal(page.requests[1].signal.aborted, true)
})
