import { useEffect, useRef, type ReactNode } from 'react'

const focusable = 'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex]:not([tabindex="-1"])'

export function AssistantDialog({ open, onClose, children }: {
  open: boolean; onClose: () => void; children: ReactNode
}) {
  const panel = useRef<HTMLElement>(null)

  useEffect(() => {
    const dialog = panel.current
    if (!open || !dialog) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const background: HTMLElement[] = []
    let branch: HTMLElement = dialog
    while (branch.parentElement) {
      const parent = branch.parentElement
      for (const element of parent.children) {
        if (element instanceof HTMLElement && element !== branch && !element.hasAttribute('data-modal-backdrop')) background.push(element)
      }
      if (parent === document.body) break
      branch = parent
    }
    const oldInert = background.map(element => element.hasAttribute('inert'))
    background.forEach(element => element.setAttribute('inert', ''))
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const items = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusable))
      .filter(element => !element.closest('[hidden], [inert]') && getComputedStyle(element).display !== 'none' && getComputedStyle(element).visibility !== 'hidden')
    const first = () => items()[0] ?? dialog
    first().focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onClose()
      } else if (event.key === 'Tab') {
        const controls = items()
        const start = controls[0] ?? dialog
        const end = controls.at(-1) ?? dialog
        if (controls.length === 0 || (event.shiftKey && (document.activeElement === start || document.activeElement === dialog)) ||
          (!event.shiftKey && document.activeElement === end)) {
          event.preventDefault()
          ;(event.shiftKey ? end : start).focus()
        }
      }
    }
    const containFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !dialog.contains(event.target)) first().focus()
    }
    dialog.addEventListener('keydown', keydown)
    document.addEventListener('focusin', containFocus)
    return () => {
      dialog.removeEventListener('keydown', keydown)
      document.removeEventListener('focusin', containFocus)
      background.forEach((element, index) => { if (!oldInert[index]) element.removeAttribute('inert') })
      document.body.style.overflow = oldOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [open, onClose])

  return <>
    {open && <div className="assistant-backdrop" data-modal-backdrop="" onClick={onClose} aria-hidden="true" />}
    <aside ref={panel} className={`assistant-panel ${open ? 'is-open' : ''}`} role="dialog" aria-modal={open ? true : undefined}
      aria-labelledby="assistant-title" tabIndex={-1} hidden={!open}>
      {children}
    </aside>
  </>
}
