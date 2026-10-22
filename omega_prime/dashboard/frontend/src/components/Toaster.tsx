import { useEffect, useRef } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import type { DashEvent } from '../lib/events'
import { formatSydneyClock } from '../lib/time'
import { taskHref } from '../lib/routes'

export const TOAST_MS: Record<DashEvent['severity'], number> = { info: 6_000, success: 6_000, warning: 9_000, error: 12_000 }
const ICONS: Record<DashEvent['severity'], string> = { info: 'i', success: '✓', warning: '!', error: '×' }

function Toast({ toast, onDismiss }: { toast: DashEvent; onDismiss: (id: string) => void }) {
  const reduced = useReducedMotion()
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const remaining = useRef(TOAST_MS[toast.severity])
  const started = useRef(0)
  // Hover and keyboard focus hold the toast independently; the countdown
  // resumes only once neither is left.
  const hovered = useRef(false)
  const focused = useRef(false)
  const start = () => {
    if (hovered.current || focused.current) return
    clearTimeout(timer.current)
    started.current = Date.now()
    timer.current = setTimeout(() => onDismiss(toast.id), remaining.current)
  }
  const pause = () => {
    if (timer.current === undefined) return
    clearTimeout(timer.current)
    timer.current = undefined
    remaining.current = Math.max(1_000, remaining.current - (Date.now() - started.current))
  }
  const hold = (kind: 'hover' | 'focus', on: boolean) => {
    if (kind === 'hover') hovered.current = on
    else focused.current = on
    if (hovered.current || focused.current) pause()
    else start()
  }
  useEffect(() => { start(); return () => clearTimeout(timer.current) }, [])
  const href = toast.taskId ? taskHref(toast.taskId) : null
  return <motion.li layout={!reduced} className={`toast toast-${toast.severity}`} role={toast.severity === 'error' ? 'alert' : 'status'} data-type={toast.type}
    initial={reduced ? false : { opacity: 0, y: 14, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={reduced ? { opacity: 0 } : { opacity: 0, x: 40 }} transition={{ duration: reduced ? 0 : 0.18 }}
    onMouseEnter={() => hold('hover', true)} onMouseLeave={() => hold('hover', false)} onFocus={() => hold('focus', true)}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) hold('focus', false) }}>
    <span className="toast-icon" aria-hidden="true">{ICONS[toast.severity]}</span>
    <div className="toast-body"><p className="toast-title">{toast.title}<time dateTime={toast.at}>{formatSydneyClock(toast.at)}</time></p><p className="toast-summary">{toast.summary}</p>{href && <a href={href} onClick={() => onDismiss(toast.id)}>Open task</a>}</div>
    <button type="button" className="toast-close" aria-label={`Dismiss ${toast.title}`} onClick={() => onDismiss(toast.id)}>×</button>
    <span className="toast-timer" style={{ animationDuration: `${TOAST_MS[toast.severity]}ms` }} aria-hidden="true" />
  </motion.li>
}

export function Toaster({ toasts, onDismiss }: { toasts: DashEvent[]; onDismiss: (id: string) => void }) {
  return <section className="toaster" aria-label="Notifications"><ol><AnimatePresence initial={false}>{toasts.map(toast => <Toast key={toast.id} toast={toast} onDismiss={onDismiss} />)}</AnimatePresence></ol></section>
}
