import { assistantResponseSchema, requestJson } from './api.ts'
import type { AssistantResponse, Forecast } from './api.ts'
import type { Direction } from './dashboard-data.ts'
import type { Geography } from './geography.ts'

const corridorRegions = { '39': 'kaliningrad', '25': 'primorye', '41': 'kamchatka' } as const

export function assistantSelection(geography: Geography, direction: Direction) {
  return {
    region: corridorRegions[geography.corridor as keyof typeof corridorRegions],
    direction,
    mode: 'sea' as const,
    end_year: 2030 as const,
    study_city: geography.studyCity,
    other_region: geography.otherRegion,
    other_city: geography.otherCity,
  }
}
export type AssistantSelection = ReturnType<typeof assistantSelection>
export type AssistantMessage = { role: 'user' | 'assistant' | 'system'; content: string; createdAt: string }

export function appendAssistantReply(history: AssistantMessage[], previousContext: Forecast | null, response: AssistantResponse, createdAt: string, question?: string): AssistantMessage[] {
  const changed = previousContext !== null && (previousContext.model_version !== response.context.model_version ||
    previousContext.data_version !== response.context.data_version)
  const messages: AssistantMessage[] = changed ? [
    { role: 'system', content: 'Версия модели или данных изменилась. Предыдущий диалог очищен.', createdAt },
    ...(question === undefined ? [] : [{ role: 'user' as const, content: question, createdAt }]),
  ] : history
  return [
    ...messages,
    ...(question === undefined ? [{ role: 'system' as const, content: 'Автоматическая сводка по годовому прогнозу линии', createdAt }] : []),
    { role: 'assistant', content: response.answer, createdAt },
  ]
}

export function isAssistantSelectionSupported(selection: AssistantSelection): boolean {
  return selection.region === 'kaliningrad' && selection.study_city === 'all' &&
    selection.other_region === 'all' && selection.other_city === 'all'
}

export async function requestAssistant(base: string, selection: AssistantSelection, signal: AbortSignal, question?: string): Promise<AssistantResponse> {
  if (!isAssistantSelectionSupported(selection)) throw new Error('Для выбранной географии и детализации контекст не подключён.')
  const query = new URLSearchParams(Object.entries(selection).map(([key, value]) => [key, String(value)]))
  const endpoint = question === undefined ? 'summary?' + query : 'ask'
  return requestJson(base + '/llm/' + endpoint, value => {
    const response = assistantResponseSchema.parse(value)
    if (response.context.direction !== selection.direction || response.context.predictions.at(-1)?.period !== String(selection.end_year)) {
      throw new Error('API вернул контекст для другого направления или горизонта.')
    }
    return response
  }, signal, 65000, question === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...selection, question }),
  })
}

type RequestHandlers<T> = {
  start: () => void
  success: (response: T) => void
  error: (cause: unknown) => void
  settled: () => void
}

/** Own the request lifetime so cancelled dialogs cannot receive any callbacks. */
export function createAssistantRequests() {
  let generation = 0
  let pending: AbortController | null = null
  return {
    cancel() {
      generation += 1
      pending?.abort()
      pending = null
    },
    async run<T>(request: (signal: AbortSignal) => Promise<T>, handlers: RequestHandlers<T>) {
      if (pending) return
      const controller = new AbortController()
      const current = ++generation
      pending = controller
      const active = () => current === generation && pending === controller && !controller.signal.aborted
      handlers.start()
      try {
        const response = await request(controller.signal)
        if (active()) handlers.success(response)
      } catch (cause) {
        if (active()) handlers.error(cause)
      } finally {
        if (active()) {
          pending = null
          handlers.settled()
        }
      }
    },
  }
}
