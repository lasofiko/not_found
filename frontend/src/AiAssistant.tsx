import { useEffect, useState } from 'react'

type Widget = {
  init: (config: { channelId: string; locale: string; theme: string; chatContainer: { container: string; hideClose: boolean } }) => void | Promise<void>
  openChat: () => void
  closeChat: () => void
  update: (config: { theme: string }) => void
}
declare global {
  interface Window { AiStudioChatWidget?: Widget; AiStudioChatWidgetConfig?: { manualMount: boolean } }
}
let pending: Promise<Widget> | undefined
function loadWidget(): Promise<Widget> {
  if (window.AiStudioChatWidget) return Promise.resolve(window.AiStudioChatWidget)
  if (pending) return pending
  pending = new Promise<Widget>((resolve, reject) => {
    window.AiStudioChatWidgetConfig = { manualMount: true }
    const script = document.createElement('script')
    script.src = 'https://widget.aistudio.yandexcloud.net/scripts/widget-loader.js?channel-id=aacpim45iu5ubg29jt66'
    script.async = true
    const fail = () => {
      clearTimeout(timer)
      script.remove()
      pending = undefined
      reject(new Error('Сервис помощника недоступен. Прогноз и калькулятор продолжают работать.'))
    }
    const timer = setTimeout(fail, 10000)
    script.onerror = fail
    script.onload = () => {
      const widget = window.AiStudioChatWidget
      if (!widget) { fail(); return }
      clearTimeout(timer)
      resolve(widget)
    }
    document.head.appendChild(script)
  })
  return pending
}

export function AiAssistant({ open, theme, onOpen }: { open: boolean; theme: string; onOpen: () => void }) {
  const [state, setState] = useState('Помощник подключается по запросу.')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!open) return
    let active = true
    let mounted: Widget | undefined
    Promise.resolve().then(async () => {
      if (!active) return
      setState('Загрузка помощника…')
      try {
        const widget = await loadWidget()
        if (!active) return
        await widget.init({ channelId: 'aacpim45iu5ubg29jt66', locale: 'ru', theme: document.documentElement.style.colorScheme || 'dark', chatContainer: { container: '#yandex-chat-container', hideClose: true } })
        if (!active) { widget.closeChat(); return }
        mounted = widget
        widget.openChat()
        setState('Помощник подключён.')
      } catch {
        if (active) setState('Сервис помощника недоступен. Прогноз и калькулятор продолжают работать.')
      }
    })
    return () => {
      active = false
      try { mounted?.closeChat() } catch { /* Сбой внешнего SDK не прерывает приложение. */ }
    }
  }, [open, retry])
  useEffect(() => {
    try { window.AiStudioChatWidget?.update({ theme }) } catch { /* Тема основного интерфейса независима от SDK. */ }
  }, [theme])
  return <>
    <div className="assistant-context">
      <p role="status">{state}</p>
      <p>Контекст прогноза автоматически не передаётся. Ответы сервиса требуют проверки источников.</p>
      <button className="button-secondary" type="button" onClick={() => { onOpen(); setRetry(value => value + 1) }}>{open ? 'Повторить подключение' : 'Подключить помощника'}</button>
    </div>
    <div id="yandex-chat-container" className="yandex-chat-container" aria-label="Диалог с AI-помощником" />
  </>
}

