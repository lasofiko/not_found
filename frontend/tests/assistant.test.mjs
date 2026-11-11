import assert from 'node:assert/strict'
import test from 'node:test'
import { appendAssistantReply, assistantSelection, createAssistantRequests, isAssistantSelectionSupported, requestAssistant } from '../src/assistant.ts'

const geography = { corridor: '39', studyCity: 'all', otherRegion: 'all', otherCity: 'all' }

function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function dialog(requests) {
  const state = { messages: [], error: null, loading: false, input: '' }
  return {
    state,
    clear() { requests.cancel(); Object.assign(state, { messages: [], error: null, loading: false, input: '' }) },
    run(reply, question = '') {
      return requests.run(signal => { reply.signal = signal; return reply.promise }, {
        start() { state.loading = true; state.input = ''; if (question) state.messages.push(question) },
        success(answer) { state.messages.push(answer) },
        error(error) { state.error = error.message; state.input = question },
        settled() { state.loading = false },
      })
    },
  }
}

for (const oldOutcome of ['success', 'error']) {
  test(`очистка диалога игнорирует старые ${oldOutcome}, сохраняя новую сводку в загрузке`, async () => {
    const page = dialog(createAssistantRequests())
    const firstSummary = deferred()
    const opened = page.run(firstSummary)
    firstSummary.resolve('Начальная сводка')
    await opened
    const ask = deferred()
    const oldRequest = page.run(ask, 'Старый вопрос')
    page.clear()
    assert.equal(ask.signal.aborted, true)
    const summary = deferred()
    const currentRequest = page.run(summary)
    // Simulate a provider/fetch that has already completed and ignores abort.
    if (oldOutcome === 'success') ask.resolve('Запоздалый ответ')
    else ask.reject(new Error('Запоздалая ошибка'))
    await oldRequest
    assert.deepEqual(page.state, { messages: [], error: null, loading: true, input: '' })
    summary.resolve('Новая сводка')
    await currentRequest
    assert.deepEqual(page.state, { messages: ['Новая сводка'], error: null, loading: false, input: '' })
  })
}

test('смена контекста и повторная очистка отменяют сводки; повторный запрос не начинается параллельно', async () => {
  const requests = createAssistantRequests()
  const page = dialog(requests)
  const old = deferred()
  const initial = page.run(old)
  let duplicateStarted = false
  await requests.run(async () => 'duplicate', {
    start() { duplicateStarted = true }, success() {}, error() {}, settled() {},
  })
  assert.equal(duplicateStarted, false)
  page.clear()
  const second = deferred()
  const intermediate = page.run(second)
  page.clear()
  const third = deferred()
  const current = page.run(third)
  old.resolve('старый контекст')
  second.reject(new Error('старый контекст'))
  await Promise.all([initial, intermediate])
  assert.equal(old.signal.aborted, true)
  assert.equal(second.signal.aborted, true)
  assert.equal(page.state.loading, true)
  third.resolve('Актуальный контекст')
  await current
  assert.deepEqual(page.state.messages, ['Актуальный контекст'])
})

test('география ассистента следует применённому выбору и не обещает городской прогноз', () => {
  for (const direction of ['inbound', 'outbound']) {
    assert.deepEqual(assistantSelection(geography, direction), {
      region: 'kaliningrad', direction, mode: 'sea', end_year: 2030,
      study_city: 'all', other_region: 'all', other_city: 'all',
    })
    assert.equal(isAssistantSelectionSupported(assistantSelection(geography, direction)), true)
  }
  for (const change of [
    { corridor: '25' }, { corridor: '41' }, { studyCity: 'manual:Балтийск' },
    { otherRegion: '47' }, { otherCity: 'manual:Усть-Луга' },
  ]) assert.equal(isAssistantSelectionSupported(assistantSelection({ ...geography, ...change }, 'inbound')), false)
})

function forecast(direction = 'outbound') {
  const score = { MAE: 1, WAPE: 2, observations: 2 }
  const stages = { development: score, holdout: score }
  return {
    status: 'ready', result_type: 'forecast', target_id: 'ust_luga_kaliningrad_ferry', target_name: 'Тестовая линия',
    region: 'kaliningrad', direction, mode: 'sea', unit: 'tonnes',
    geography: { origin: 'Балтийск', destination: 'Усть-Луга', scope: 'Только морская линия', route_id: 'line' },
    training_period: { start: '2015', end: '2023' }, forecast_origin: 2023, observations: 9, selected_model: 'naive',
    data_version: 'test-data', model_version: 'test-model', generated_at: '2026-11-11',
    predictions: Array.from({ length: 7 }, (_, index) => ({
      period: String(2024 + index), period_granularity: 'year', horizon_years: index + 1, value: 100,
      interval: { '80': { lower: 90, upper: 110 }, '95': { lower: 80, upper: 120 } },
    })),
    provenance: { source_ids: ['test-source'], sources: [{ source_id: 'test-source', organization: 'Тестовый источник', url: 'https://example.org/source' }] },
    metrics: { selected: stages, baseline: stages, selection_period: '2020–2021', holdout_period: '2022–2023', evaluation_horizon_years: 1, methodological_status: 'test' },
    warnings: ['Не весь коридор'],
  }
}

test('новая версия модели или данных очищает прежние ответы, сохраняя текущий вопрос и его ответ', () => {
  const context = forecast()
  const response = { answer: 'Первая сводка', result_type: 'interpretation', context }
  const initial = appendAssistantReply([], null, response, '10:00')
  assert.deepEqual(initial.map(({ role }) => role), ['system', 'assistant'])
  const question = { role: 'user', content: 'Текущий вопрос', createdAt: '10:01' }
  const history = [...initial, question]
  const same = appendAssistantReply(history, context, { ...response, answer: 'Ответ той же версии' }, '10:02', question.content)
  assert.deepEqual(same.slice(0, -1), history)
  for (const version of ['model_version', 'data_version']) {
    const updated = appendAssistantReply(history, context, { ...response, answer: 'Ответ новой версии', context: { ...context, [version]: 'new-version' } }, '10:02', question.content)
    assert.deepEqual(updated.map(({ role }) => role), ['system', 'user', 'assistant'])
    assert.match(updated[0].content, /Предыдущий диалог очищен/)
    assert.equal(updated[1].content, question.content)
    assert.equal(updated[2].content, 'Ответ новой версии')
    assert.equal(updated.some(({ content }) => content === 'Первая сводка'), false)
  }
  assert.deepEqual(history, [...initial, question], 'исходная история не должна изменяться')
})

test('summary и ask передают только выбранный срез; ответ сохраняет тот же проверенный контекст', async () => {
  const original = global.fetch
  const selection = assistantSelection(geography, 'outbound')
  const context = forecast()
  const calls = []
  global.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ answer: 'Интерпретация', result_type: 'interpretation', context })
  }
  try {
    const signal = new AbortController().signal
    assert.deepEqual(await requestAssistant('/api/v1', selection, signal), { answer: 'Интерпретация', result_type: 'interpretation', context })
    const url = new URL(calls[0].url, 'http://localhost')
    assert.equal(url.pathname, '/api/v1/llm/summary')
    assert.deepEqual(Object.fromEntries(url.searchParams), Object.fromEntries(Object.entries(selection).map(([key, value]) => [key, String(value)])))
    assert.equal(calls[0].options.body, undefined)
    await requestAssistant('/api/v1', selection, signal, 'Какой охват?')
    assert.equal(calls[1].url, '/api/v1/llm/ask')
    assert.equal(calls[1].options.method, 'POST')
    assert.deepEqual(JSON.parse(calls[1].options.body), { ...selection, question: 'Какой охват?' })
    await assert.rejects(requestAssistant('/api/v1', assistantSelection({ ...geography, corridor: '41' }, 'outbound'), signal), /контекст не подключён/)
    assert.equal(calls.length, 2, 'неподдержанный срез не должен вызывать LLM')
    await assert.rejects(requestAssistant('/api/v1', { ...selection, direction: 'inbound' }, signal), /другого направления/)
    context.predictions.pop()
    await assert.rejects(requestAssistant('/api/v1', selection, signal), /другого направления или горизонта/)
  } finally { global.fetch = original }
})
