import { useEffect, useRef, useState } from 'react'
import { RotateCcw, Sparkles, X } from 'lucide-react'

type Theme = 'light' | 'dark'
type Widget = {
  init: (config: {
    channelId: string
    locale: 'ru'
    theme: Theme
    chat: { appearance: Record<string, string> }
    chatContainer: { container: string; hideClose: boolean }
  }) => void
  openChat: () => void
  closeChat: () => void
  update: (config: { theme: Theme }) => void
}
type ChatWindow = Window & {
  AiStudioChatWidget?: Widget
  AiStudioChatWidgetConfig?: { manualMount: boolean }
}
const channelId = 'aacpim45iu5ubg29jt66'
const scriptId = 'ai-studio-loader'
const getWidget = () => (window as ChatWindow).AiStudioChatWidget

function chatAppearance(theme: Theme): Record<string, string> {
  const dark = theme === 'dark'
  const background = dark ? '#15182A' : '#FCFCFE'
  const raised = dark ? '#1B1E32' : '#F5F5FA'
  const text = dark ? '#F0F0F7' : '#22263A'
  const secondary = dark ? '#A3A8BC' : '#697086'
  return {
    bgPrimary: background, colorBgPrimary: background,
    bgSecondary: raised, colorBgSecondary: raised,
    textPrimary: text, textSecondary: secondary,
    bgMessageUser: dark ? '#342C57' : '#E9E4FB',
    colorBgMessageUser: dark ? '#342C57' : '#E9E4FB',
    bgMessageAssistant: raised, colorBgMessageAssistant: raised,
    textMessageUser: text, textMessageAssistant: text,
    borderColor: dark ? '#3A3F58' : '#D4D5E2',
    accentColor: dark ? '#A395F1' : '#705FD0',
    lineBrand: dark ? '#52C5B9' : '#207D75',
    headerBackground: background, emptyContainerBackground: background,
    chatContentBackground: background, chatContainerBackground: background,
    chatContainerHeaderBackground: background,
    chatContainerContentBackground: background,
    chatContainerFooterBackground: background,
    chatContainerContentEmptyBackground: background,
    chatContainerContentChatBackground: background,
    chatContainerFooterEmptyBackground: background,
    chatContainerFooterChatBackground: background,
    ciColorProgress1: '#705FD0', ciColorProgress2: '#52C5B9', ciColorProgress3: '#A395F1',
  }
}

export function AiAssistant({ open, onClose, theme }: {
  open: boolean
  onClose: () => void
  theme: Theme
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const container = useRef<HTMLDivElement>(null)
  const initialized = useRef<Theme | null>(null)
  const loaded = useRef(false)
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal()
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])

  useEffect(() => {
    if (!open) return
    const node = container.current
    if (!node) return
    let disposed = false
    const frames = new Set<HTMLIFrameElement>()
    const fail = () => { if (!disposed) setStatus('unavailable') }
    const timeout = window.setTimeout(fail, 15000)
    const ready = () => {
      if (disposed) return
      loaded.current = true
      window.clearTimeout(timeout)
      setStatus('ready')
    }
    const watchFrames = () => {
      for (const frame of node.querySelectorAll('iframe')) {
        if (frames.has(frame)) continue
        frames.add(frame)
        frame.title ||= 'Чат Yandex AI Studio'
        frame.addEventListener('load', ready)
        frame.addEventListener('error', fail)
      }
    }
    const observer = new MutationObserver(watchFrames)
    observer.observe(node, { childList: true, subtree: true })
    watchFrames()

    const mount = () => {
      const widget = getWidget()
      if (disposed || !widget) return
      try {
        if (initialized.current !== theme) {
          widget.init({
            channelId, locale: 'ru', theme,
            chat: { appearance: chatAppearance(theme) },
            chatContainer: { container: '#yandex-chat-container', hideClose: true },
          })
          initialized.current = theme
        }
        widget.update({ theme })
        widget.openChat()
        watchFrames()
        if (loaded.current && node.querySelector('iframe')) ready()
      } catch { fail() }
    }

    let script = document.getElementById(scriptId) as HTMLScriptElement | null
    if (getWidget()) mount()
    else {
      if (!script) {
        ;(window as ChatWindow).AiStudioChatWidgetConfig = { manualMount: true }
        script = document.createElement('script')
        script.id = scriptId
        script.src = `https://widget.aistudio.yandexcloud.net/scripts/widget-loader.js?channel-id=${channelId}`
        script.async = true
        script.addEventListener('load', mount)
        script.addEventListener('error', fail)
        document.head.appendChild(script)
      } else {
        script.addEventListener('load', mount)
        script.addEventListener('error', fail)
      }
    }
    return () => {
      disposed = true
      clearTimeout(timeout)
      observer.disconnect()
      for (const frame of frames) {
        frame.removeEventListener('load', ready)
        frame.removeEventListener('error', fail)
      }
      script?.removeEventListener('load', mount)
      script?.removeEventListener('error', fail)
      try { getWidget()?.closeChat() } catch { /* The external service may be unavailable. */ }
    }
  }, [open, theme, attempt])

  const retry = () => {
    try { getWidget()?.closeChat() } catch { /* Retry still remains available. */ }
    document.getElementById(scriptId)?.remove()
    container.current?.replaceChildren()
    initialized.current = null
    loaded.current = false
    setStatus('loading')
    setAttempt((value) => value + 1)
  }

  return (
    <dialog ref={dialog} className="assistant-panel" onCancel={onClose} onClose={onClose} aria-labelledby="assistant-title" onPointerDown={(event) => {
      if (event.target !== event.currentTarget) return
      const rect = event.currentTarget.getBoundingClientRect()
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose()
    }}>
      <header className="assistant-header">
        <Sparkles size={24} />
        <div><h2 id="assistant-title">AI-ассистент</h2><p>Yandex AI Studio · внешний сервис</p></div>
        <button className="icon-button" onClick={onClose} aria-label="Закрыть AI-ассистента"><X size={20} /></button>
      </header>
      {status !== 'ready' && <div className="chat-status" role="status">
        <p>{status === 'loading' ? 'Загружаем внешний чат…' : 'Внешний чат сейчас недоступен. Проверьте подключение к интернету и повторите загрузку.'}</p>
        {status === 'unavailable' && <button className="button-secondary" onClick={retry}><RotateCcw size={16} />Повторить загрузку</button>}
      </div>}
      <div ref={container} id="yandex-chat-container" className="yandex-chat-container" aria-label="Диалог с AI-ассистентом" aria-busy={status === 'loading'} />
    </dialog>
  )
}
