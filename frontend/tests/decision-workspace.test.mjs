import assert from 'node:assert/strict'
import test from 'node:test'
import { act, browser, decisionComponent, createElement as h } from './decision-dom.mjs'

const { DecisionWorkspace, DecisionAssistant } = await decisionComponent()

function syntheticSnapshot(id = 'synthetic-decision-test-only') {
  const context = { region: 'kaliningrad', target_id: 'ust_luga_kaliningrad_ferry', route: 'Усть-Луга — Балтийск', scope: 'Синтетический пример теста', mode: 'sea', direction: 'inbound', period: '2027', granularity: 'year', model_version: null, data_version: null, forecast_origin: null }
  const snapshot = { comparison_id: id, created_at: '2026-11-12T00:00:00Z', expires_at: new Date(Date.now() + 1800000).toISOString(), status: 'scenario', unit: 'tonnes', context, inputs: {}, demand_tonnes: 150,
    variants: [
      { id: 'baseline', label: 'Без действия', name: 'Без действия', capacity_delta_tonnes: 0, scenario_capacity_tonnes: 100, scenario_shortfall_tonnes: 50, shortfall_reduction_tonnes: 0, scenario_spare_capacity_tonnes: 0, coverage_share: 2 / 3 },
      { id: 'reserve_ferry', label: 'А', name: 'Резервный паром на время ремонтов', capacity_delta_tonnes: 20, scenario_capacity_tonnes: 120, scenario_shortfall_tonnes: 30, shortfall_reduction_tonnes: 20, scenario_spare_capacity_tonnes: 0, coverage_share: .8 },
      { id: 'additional_ferry', label: 'В', name: 'Постоянный дополнительный паром', capacity_delta_tonnes: 70, scenario_capacity_tonnes: 170, scenario_shortfall_tonnes: 0, shortfall_reduction_tonnes: 50, scenario_spare_capacity_tonnes: 20, coverage_share: 1 },
    ].map(row => ({ ...row, demand_tonnes: 150, capacity_tonnes: 100, assumption_source: '<script>unsafe</script> Допущение пользователя', status: 'scenario' })),
    thresholds: { required_delta_tonnes: 50, grid: { demand_values: [0, 150, 300], delta_values: [0, 50, 100], shortfalls: [[0, 50, 200], [0, 0, 150], [0, 0, 100]], boundary_delta_tonnes: [0, 50, 200] } },
    passports: {}, sources: [], warnings: ['Синтетический пример; эффект действия не подтверждён.'], forecast_basis: null, direction_profile: null }
  const makePassport = (title, value) => ({ title, definition: 'Синтетическая проверка интерфейса', value, unit: 'tonnes', period: '2027', geography: context.route, direction: 'inbound', result_type: 'scenario', source: 'Допущение пользователя', source_url: null, original_unit: 'tonnes', conversion: 'Без пересчёта', formula: 'max(0, demand - capacity - delta)', data_version: null, model_version: null, forecast_origin: null, calculated_at: snapshot.created_at, machine_validation: 'Тестовая машинная проверка', human_review: null, limitations: ['Эффект не доказан'] })
  snapshot.variants.forEach((row, index) => { for (const field of ['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes', 'scenario_capacity_tonnes', 'scenario_shortfall_tonnes', 'shortfall_reduction_tonnes', 'scenario_spare_capacity_tonnes', 'coverage_share']) snapshot.passports[`variants.${index}.${field}`] = makePassport(field === 'scenario_shortfall_tonnes' ? 'Непокрытая нагрузка' : 'Показатель', row[field]) })
  snapshot.passports['thresholds.required_delta_tonnes'] = makePassport('Требуемая дополнительная возможность', 50)
  return snapshot
}
const coverage = { checked_at: '2026-11-12', rows: [], warnings: [] }
function fill(page) {
  const values = { period: '2027', demand_tonnes: '150', capacity_tonnes: '100', demand_source: 'Синтетический тест', capacity_source: 'Допущение теста', reserve_delta: '20', reserve_source: 'Допущение А', additional_delta: '70', additional_source: 'Допущение Б' }
  for (const [name, value] of Object.entries(values)) page.container.querySelector(`[name="${name}"]`).value = value
}
const configFetch = url => url === '/config.json' ? Response.json({ apiBase: '/custom-api' }) : url.endsWith('/coverage') ? Response.json(coverage) : null

test('смена территории отменяет независимый профиль и не показывает запоздавшие данные другой линии', async t => {
  const page = browser(); t.after(() => page.close()); let pending
  global.fetch = async (url, options) => {
    const known = configFetch(url); if (known) return known
    if (url.includes('/direction-profile?')) return new Promise(resolve => { pending = { signal: options.signal, resolve } })
    throw new Error('Неожиданный адрес ' + url)
  }
  await page.render(h(DecisionWorkspace, { onSnapshotChange() {}, onOpenAssistant() {} }))
  const load = [...page.container.querySelectorAll('button')].find(button => button.textContent === 'Показать ввоз и вывоз вместе')
  await act(async () => load.click())
  const region = page.container.querySelector('[name="region"]'); region.value = 'kamchatka'
  await act(async () => page.event(region, 'change'))
  assert.equal(pending.signal.aborted, true)
  await act(async () => pending.resolve(Response.json({status: 'not_connected', unit: 'tonnes', rows: [], passports: {},
    sources: [], warnings: ['Старый профиль другой линии'], model_version: null, data_version: null, forecast_origin: null})))
  assert.equal(page.container.querySelector('.decision-profile'), null)
  assert.doesNotMatch(page.container.textContent, /Старый профиль другой линии/)
})

test('активный React: серверные числа, паспорт, клавиатура и безопасный текст', async t => {
  const page = browser(); t.after(() => page.close()); let applied = null; let input = null
  global.fetch = async (url, options) => {
    const known = configFetch(url); if (known) return known
    if (url.endsWith('/comparisons')) { input = JSON.parse(options.body); return Response.json(syntheticSnapshot()) }
    throw new Error('Неожиданный адрес ' + url)
  }
  await page.render(h(DecisionWorkspace, { onSnapshotChange: value => { applied = value }, onOpenAssistant() {} }))
  assert.match(page.container.querySelector('.decision-actions').textContent, /В · Постоянный дополнительный паром/)
  assert.equal(page.container.querySelector('[name="capacity_tonnes"]').value, '')
  fill(page)
  await act(async () => page.event(page.container.querySelector('form'), 'submit'))
  assert.equal(input.actions.additional_ferry.delta_tonnes, 70)
  assert.equal(applied.comparison_id, 'synthetic-decision-test-only')
  const rows = page.container.querySelectorAll('[aria-label="Числовое сравнение решений"] tbody tr')
  assert.equal(rows.length, 3)
  assert.match(rows[0].textContent, /50/); assert.match(rows[1].textContent, /30/); assert.match(rows[2].textContent, /170/)
  assert.match(page.container.querySelector('.decision-bars').textContent, /непокрыто 50/)
  assert.equal(page.container.querySelector('script'), null)
  const button = rows[1].querySelectorAll('button')[4]; button.focus()
  await act(async () => button.click())
  const dialog = page.container.querySelector('[role="dialog"]')
  assert.match(dialog.textContent, /Непокрытая нагрузка/)
  assert.match(dialog.textContent, /Человеческая сверка/)
  assert.match(dialog.textContent, /Сведения о завершённой сверке отсутствуют/)
  assert.equal(document.activeElement.getAttribute('aria-label'), 'Закрыть паспорт')
  await act(async () => page.key(document.activeElement, 'Tab', true))
  assert.ok(dialog.contains(document.activeElement))
  await act(async () => page.key(document.activeElement, 'Escape'))
  assert.equal(page.container.querySelector('[role="dialog"]'), null)
  assert.equal(document.activeElement, button)
  await act(async () => page.event(page.container.querySelector('[name="capacity_tonnes"]'), 'input'))
  assert.equal(applied, null)
  assert.equal(page.container.querySelector('.decision-report'), null)
  assert.match(page.container.textContent, /Предыдущее сравнение неактуально/)
})

test('смена направления отменяет запрос и отбрасывает запоздалый ответ', async t => {
  const page = browser(); t.after(() => page.close()); const calls = []; let applied = null
  global.fetch = async (url, options) => {
    const known = configFetch(url); if (known) return known
    if (url.endsWith('/comparisons')) return new Promise(resolve => calls.push({ signal: options.signal, resolve }))
    throw new Error('Неожиданный адрес ' + url)
  }
  await page.render(h(DecisionWorkspace, { onSnapshotChange: value => { applied = value }, onOpenAssistant() {} }))
  fill(page)
  await act(async () => page.event(page.container.querySelector('form'), 'submit'))
  assert.equal(calls.length, 1)
  const direction = page.container.querySelector('[name="direction"]'); direction.value = 'outbound'
  await act(async () => page.event(direction, 'change'))
  assert.equal(calls[0].signal.aborted, true)
  await act(async () => calls[0].resolve(Response.json(syntheticSnapshot('old-result'))))
  assert.equal(applied, null)
  assert.equal(page.container.querySelector('.decision-results'), null)
})

test('частичный подробный разбор виден как частичный, дублирующий запуск блокируется и смена снимка отменяет запрос', async t => {
  const page = browser(); t.after(() => page.close()); const calls = []
  global.fetch = async (url, options) => {
    const known = configFetch(url); if (known) return known
    if (url.endsWith('/analysis')) return new Promise(resolve => calls.push({ signal: options.signal, resolve }))
    throw new Error('Неожиданный адрес ' + url)
  }
  await page.render(h(DecisionAssistant, { key: 'first', snapshot: syntheticSnapshot() }))
  const button = [...page.container.querySelectorAll('button')].find(button => button.textContent === 'Подробный разбор')
  await act(async () => { button.click(); button.click() })
  assert.equal(calls.length, 1)
  await act(async () => calls[0].resolve(Response.json({ comparison_id: 'synthetic-decision-test-only', status: 'partial', agents: [{ agent_id: 'evidence_analyst', status: 'unavailable', findings: [], evidence_refs: [], assumptions: [], limitations: ['Таймаут'], questions_for_user: [] }], sections: [{ title: 'Почему получился такой результат', items: ['Сравнение по допущениям'] }], warnings: ['Один специалист недоступен'] })))
  assert.match(page.container.textContent, /Частичный разбор/)
  await act(async () => button.click())
  assert.equal(calls.length, 2)
  await page.render(h(DecisionAssistant, { key: 'second', snapshot: syntheticSnapshot('new-result') }))
  assert.equal(calls[1].signal.aborted, true)
  await act(async () => calls[1].resolve(Response.json({ comparison_id: 'synthetic-decision-test-only', status: 'complete', agents: [], sections: [{ title: 'Старый вывод', items: ['Устаревшее пояснение'] }], warnings: [] })))
  assert.doesNotMatch(page.container.textContent, /Устаревшее пояснение/)
})

test('смена версии на сервере делает снимок неактуальным; единица отображения не запускает расчёт', async t => {
  const page = browser(); t.after(() => page.close()); let calculations = 0; let applied = null
  global.fetch = async (url) => {
    const known = configFetch(url); if (known) return known
    if (url.endsWith('/comparisons')) { calculations++; return Response.json(syntheticSnapshot()) }
    if (url.endsWith('/comparisons/synthetic-decision-test-only')) return Response.json({ error: { message: 'Версия модели или данных изменилась. Выполните новое сравнение.' } }, { status: 409 })
    throw new Error('Неожиданный адрес ' + url)
  }
  await page.render(h(DecisionWorkspace, { onSnapshotChange: value => { applied = value }, onOpenAssistant() {} }))
  fill(page)
  await act(async () => page.event(page.container.querySelector('form'), 'submit'))
  const units = page.container.querySelector('.decision-toolbar select'); units.value = '1000'
  await act(async () => page.event(units, 'change'))
  assert.equal(calculations, 1)
  assert.match(page.container.querySelector('.decision-results').textContent, /тыс\. т/)
  await act(async () => window.dispatchEvent(new page.dom.window.Event('focus')))
  assert.equal(applied, null)
  assert.equal(page.container.querySelector('.decision-results'), null)
  assert.match(page.container.textContent, /Версия модели или данных изменилась/)
})
