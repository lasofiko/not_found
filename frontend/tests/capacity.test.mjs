import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const script = readFileSync(new URL('../app.js', import.meta.url), 'utf8')

const responseData = (overrides = {}) => ({
  status: 'scenario',
  unit: 'tonnes',
  period: '2027-01',
  baseline_shortfall_tonnes: 20,
  scenario_shortfall_tonnes: 0,
  shortfall_reduction_tonnes: 20,
  warning: 'Расчёт по введённым допущениям; не прогноз.',
  assumption_source: 'Ввод пользователя',
  ...overrides,
})

// Minimal browser boundary: the tests interact through form events, fetch replies
// and rendered output, without reaching into the calculator's private state.
async function openCalculator({ ignoreAbort = false } = {}) {
  const handlers = new Map()
  const timers = new Map()
  const requests = []
  const attributes = new Map()
  let timerId = 0
  let now = 0
  let rendered = ''
  const button = { disabled: true }
  const output = {
    get textContent() { return rendered },
    set textContent(value) { rendered = String(value) },
    get innerHTML() { return rendered },
    set innerHTML(value) { rendered = String(value) },
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
  }
  const values = {
    period: '2027-01',
    demand_tonnes: '100',
    capacity_tonnes: '80',
    capacity_delta_tonnes: '30',
    assumption_source: 'Ввод пользователя',
  }
  const form = {
    querySelector: () => button,
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  const context = {
    document: {
      baseURI: 'http://localhost/capacity',
      querySelector: (selector) => selector === '#capacity-form' ? form : output,
      createElement: () => ({
        textContent: '',
        get innerHTML() {
          return this.textContent.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
        },
      }),
    },
    window: {
      location: { origin: 'http://localhost' },
      setTimeout: (callback, delay) => {
        timers.set(++timerId, { callback, at: now + delay })
        return timerId
      },
      clearTimeout: (id) => timers.delete(id),
    },
    URL,
    AbortController,
    Intl,
    FormData: class {
      *[Symbol.iterator]() { yield* Object.entries(values) }
    },
    fetch: (url, options) => {
      if (String(url).endsWith('config.json')) {
        return Promise.resolve({ ok: true, json: async () => ({ apiBase: '/api/v1' }) })
      }
      return new Promise((resolve, reject) => {
        requests.push({
          body: JSON.parse(options.body),
          reply: (data) => resolve({ ok: true, json: async () => data }),
        })
        if (!ignoreAbort) {
          options.signal.addEventListener('abort', () => reject(new Error('Aborted')), { once: true })
        }
      })
    },
  }
  vm.runInNewContext(script, context, { filename: 'app.js' })
  await new Promise(setImmediate)
  assert.equal(button.disabled, false, 'успешная конфигурация должна разрешать расчёт')
  return {
    button,
    output,
    requests,
    submit: () => handlers.get('submit')({ preventDefault() {} }),
    edit: (changes) => {
      Object.assign(values, changes)
      handlers.get('input')()
    },
    advance: (milliseconds) => {
      now += milliseconds
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) {
          timers.delete(id)
          timer.callback()
        }
      }
    },
  }
}

test('калькулятор не отправляет повторный запрос до завершения расчёта', async () => {
  const page = await openCalculator()
  const first = page.submit()
  assert.equal(page.button.disabled, true)
  await page.submit()
  assert.equal(page.requests.length, 1)
  assert.equal(page.requests[0].body.demand_tonnes, 100)
  page.requests[0].reply(responseData())
  await first
  assert.equal(page.button.disabled, false)
  assert.match(page.output.innerHTML, /Результат за 2027-01/)
})

test('запоздалый ответ после редактирования не подменяет новый результат', async () => {
  // Even a server reply already in flight when cancellation happens must be ignored.
  const page = await openCalculator({ ignoreAbort: true })
  const oldRequest = page.submit()
  page.edit({ period: '2027-02' })
  assert.match(page.output.textContent, /изменены/)
  assert.equal(page.button.disabled, false)
  const newRequest = page.submit()
  page.requests[1].reply(responseData({ period: '2027-02' }))
  await newRequest
  const expectedOutput = page.output.innerHTML
  page.requests[0].reply(responseData({ period: '2027-01' }))
  await oldRequest
  assert.equal(page.output.innerHTML, expectedOutput)
  assert.match(page.output.innerHTML, /Результат за 2027-02/)
  assert.equal(page.button.disabled, false)
})

test('после таймаута показана ошибка и доступна повторная попытка', async () => {
  const page = await openCalculator()
  const stalled = page.submit()
  page.advance(15000)
  await stalled
  assert.match(page.output.textContent, /15 секунд/)
  assert.equal(page.button.disabled, false)
  const retry = page.submit()
  page.requests[1].reply(responseData())
  await retry
  assert.match(page.output.innerHTML, /Результат за 2027-01/)
  assert.equal(page.button.disabled, false)
})

test('поля ответа выводятся текстом, без вставки посторонней HTML-разметки', async () => {
  const page = await openCalculator()
  const calculation = page.submit()
  page.requests[0].reply(responseData({
    warning: '<img src=x onerror=alert(1)><script>alert(2)</script>',
    assumption_source: '<svg onload=alert(3)>& источник',
  }))
  await calculation
  assert.doesNotMatch(page.output.innerHTML, /<(?:img|script|svg)\b/)
  assert.match(page.output.innerHTML, /&lt;img/)
  assert.match(page.output.innerHTML, /&lt;script&gt;/)
  assert.match(page.output.innerHTML, /&lt;svg/)
  assert.match(page.output.innerHTML, /&amp; источник/)
})
