import { useCallback, useEffect, useRef, useState } from 'react'
import type { Snapshot } from '../types'
import { appendEvents, deriveEvents, makeEvent, shouldToast, stabilizeSnapshot, type DashEvent, type EventDraft, type EventOrigin } from '../lib/events'

/** Most toasts on screen at once; older ones are dropped first. */
export const MAX_TOASTS = 4
/** A single snapshot diff that yields more toast-worthy events than this is
 *  collapsed into the first few plus one summary toast. */
export const TOAST_BURST = 3

export type EventLogState = {
  events: DashEvent[]
  toasts: DashEvent[]
  /** Record a client-side event (terminal lifecycle, user actions). */
  log: (draft: EventDraft) => void
  dismissToast: (id: string) => void
  clear: () => void
}

export function toastBatch(events: DashEvent[], at: string): DashEvent[] {
  const worthy = events.filter(shouldToast)
  if (worthy.length <= TOAST_BURST) return worthy
  const shown = worthy.slice(0, TOAST_BURST - 1)
  const rest = worthy.length - shown.length
  const severity = worthy.slice(shown.length).some(event => event.severity === 'error') ? 'error' : 'info'
  return [...shown, makeEvent({ type: 'stream.collapsed', severity, taskId: null, title: `${rest} more updates`, summary: 'Open the event stream for the full log.', payload: { collapsed: rest } }, { at, source: 'client' })]
}

/** Turn every dashboard change into structured, timestamped events: diffs of
 *  consecutive snapshots, connection transitions and errors. `origin` is the
 *  transport that delivered `data` (websocket, SSE fallback or an HTTP
 *  fetch), reported by `useDashboard` alongside it, so each event names the
 *  path it really arrived on. Feeds the event stream and the toasts. */
export function useEventLog(data: Snapshot | null, origin: EventOrigin | null, live: boolean, error: string | null): EventLogState {
  const [events, setEvents] = useState<DashEvent[]>([])
  const [toasts, setToasts] = useState<DashEvent[]>([])
  const previous = useRef<Snapshot | null>(null)
  const lastData = useRef<Snapshot | null>(null)
  const everLive = useRef(false)
  const lostAt = useRef<number | null>(null)
  const lastError = useRef<string | null>(null)

  const push = useCallback((batch: DashEvent[], toast = true) => {
    if (batch.length === 0) return
    setEvents(log => appendEvents(log, batch))
    if (!toast) return
    const next = toastBatch(batch, batch[batch.length - 1]!.at)
    if (next.length) setToasts(current => [...current, ...next].slice(-MAX_TOASTS))
  }, [])

  useEffect(() => {
    if (data === null) return
    if (lastData.current === data) return
    lastData.current = data
    const prior = previous.current
    // Diff against the last good rows of any failed source, and keep that as
    // the baseline, so an outage neither removes nor later replays its rows.
    const next = stabilizeSnapshot(prior, data)
    previous.current = next
    const at = new Date().toISOString()
    push(deriveEvents(prior, next).map(draft => makeEvent(draft, { at, source: origin ?? 'poll' })), prior !== null)
    // `origin` is committed in the same render as `data`: read here, not a trigger.
  }, [data, push])

  useEffect(() => {
    const at = new Date().toISOString()
    if (live) {
      if (lostAt.current !== null) {
        const seconds = Math.round((Date.now() - lostAt.current) / 1000)
        push([makeEvent({ type: 'connection.restored', severity: 'success', taskId: null, title: 'Live stream restored', summary: `Websocket reconnected after ${seconds}s`, payload: { offline_s: seconds } }, { at, source: 'client' })])
        lostAt.current = null
      }
      everLive.current = true
    } else if (everLive.current && lostAt.current === null) {
      lostAt.current = Date.now()
      push([makeEvent({ type: 'connection.lost', severity: 'warning', taskId: null, title: 'Live stream lost', summary: 'Falling back to polling until the websocket reconnects', payload: { transport: 'poll' } }, { at, source: 'client' })])
    }
  }, [live, push])

  useEffect(() => {
    if (error === null) { lastError.current = null; return }
    if (error === lastError.current) return
    lastError.current = error
    push([makeEvent({ type: 'dashboard.error', severity: 'error', taskId: null, title: 'Dashboard error', summary: error, payload: {} }, { at: new Date().toISOString(), source: 'client' })])
  }, [error, push])

  const log = useCallback((draft: EventDraft) => {
    push([makeEvent(draft, { at: new Date().toISOString(), source: 'client' })])
  }, [push])
  const dismissToast = useCallback((id: string) => setToasts(current => current.filter(toast => toast.id !== id)), [])
  const clear = useCallback(() => setEvents([]), [])
  return { events, toasts, log, dismissToast, clear }
}
