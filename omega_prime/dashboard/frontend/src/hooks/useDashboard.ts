import { useCallback, useEffect, useRef, useState } from 'react'
import type { FinishedItem, Snapshot, Task, WaitingItem } from '../types'

const POLL_MS = 5_000
const RECONNECT_MS = 30_000
const WS_BASE_MS = 1_000
const WS_MAX_MS = 30_000
const WS_FALLBACK_AFTER = 3
/** Most deltas held for replay while a repair fetch is outstanding. Past
 *  this the queue is dropped and the socket is reopened, because the server
 *  sends a fresh snapshot first on every /ws/events connection. */
export const MAX_PENDING_DELTAS = 200
export const REPAIR_BASE_MS = 1_000
export const REPAIR_MAX_MS = 30_000

type Stale = { reason: string; since: string }
type WsMessage =
  | { type: 'snapshot'; snapshot: Snapshot }
  | { type: 'task_changed'; task: Task }
  | { type: 'task_finished'; task: Task }
  | { type: 'waiting_added'; item: WaitingItem }
  | { type: 'waiting_cleared'; id: string }
  | { type: 'stale_changed'; id: string; stale: Stale | null }
  | { type: 'heartbeat' }

export type DashboardState = { data: Snapshot | null; loading: boolean; error: string | null; receivedAt: number | null; live: boolean; refresh: () => void }

/** Validate one /ws/events frame; the protocol shapes are the contract, so
 *  the discriminant and identity fields are checked and the named type is
 *  asserted at this boundary. Returns null for anything unparseable. */
export function parseMessage(raw: string): WsMessage | null {
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return null }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const body = parsed as Partial<WsMessage>
  switch (body.type) {
    case 'heartbeat': return { type: 'heartbeat' }
    case 'snapshot':
      return typeof body.snapshot === 'object' && body.snapshot !== null
        ? { type: 'snapshot', snapshot: body.snapshot }
        : null
    case 'task_changed':
    case 'task_finished':
      return typeof body.task === 'object' && body.task !== null && typeof body.task.id === 'string'
        ? { type: body.type, task: body.task }
        : null
    case 'waiting_added':
      return typeof body.item === 'object' && body.item !== null && typeof body.item.id === 'string'
        ? { type: 'waiting_added', item: body.item }
        : null
    case 'waiting_cleared':
      return typeof body.id === 'string' ? { type: 'waiting_cleared', id: body.id } : null
    case 'stale_changed':
      if (typeof body.id !== 'string' || typeof body.stale === 'undefined') return null
      if (body.stale !== null && typeof body.stale !== 'object') return null
      return { type: 'stale_changed', id: body.id, stale: body.stale }
    default: return null
  }
}

const STATE_RANKS: Record<string, number> = { blocked: 0, working: 1, quiet: 2, 'stopped?': 3, waiting_on_ove: 4, idle: 5, unknown: 6, done: 7, failed: 8 }
const FINISHED_CAP = 50
const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** Mirror normalize_tasks ordering so a delta-built list matches a fresh
 *  snapshot: active by state rank then id; waiting newest `since` first;
 *  finished newest `finished_at` first (undated first), capped at 50. */
export function sortActive(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => ((STATE_RANKS[a.state] ?? 6) - (STATE_RANKS[b.state] ?? 6)) || cmp(a.id, b.id))
}
export function sortWaiting(items: WaitingItem[]): WaitingItem[] {
  return [...items].sort((a, b) => {
    const as = a.since ?? null, bs = b.since ?? null
    if ((as === null) !== (bs === null)) return as === null ? 1 : -1
    return cmp(bs ?? '', as ?? '') || cmp(b.id, a.id)
  })
}
export function sortFinished(items: FinishedItem[]): FinishedItem[] {
  return [...items].sort((a, b) => {
    const af = a.finished_at ?? null, bf = b.finished_at ?? null
    if ((af === null) !== (bf === null)) return af === null ? -1 : 1
    return cmp(bf ?? '', af ?? '') || cmp(b.id, a.id)
  }).slice(0, FINISHED_CAP)
}

/** Apply one delta immutably; null means the delta cannot be applied. */
export function applyDelta(current: Snapshot, message: WsMessage): Snapshot | null {
  switch (message.type) {
    case 'task_changed': {
      const active = current.active ?? []
      const exists = active.some(task => task.id === message.task.id)
      return { ...current, active: sortActive(exists ? active.map(task => task.id === message.task.id ? message.task : task) : [...active, message.task]) }
    }
    case 'task_finished': {
      const finished = current.finished ?? []
      const exists = finished.some(item => item.id === message.task.id)
      return {
        ...current,
        active: (current.active ?? []).filter(task => task.id !== message.task.id),
        idle_sessions: (current.idle_sessions ?? []).filter(task => task.id !== message.task.id),
        finished: sortFinished(exists ? finished.map(item => item.id === message.task.id ? message.task as unknown as FinishedItem : item) : [...finished, message.task as unknown as FinishedItem]),
      }
    }
    case 'waiting_added': {
      const waiting = current.waiting_on_ove ?? []
      if (waiting.some(item => item.id === message.item.id)) return current
      return { ...current, waiting_on_ove: sortWaiting([...waiting, message.item]) }
    }
    case 'waiting_cleared':
      return { ...current, waiting_on_ove: (current.waiting_on_ove ?? []).filter(item => item.id !== message.id) }
    case 'stale_changed': {
      let matched = false
      const patch = (tasks: Task[]) => tasks.map(task => {
        if (task.id !== message.id) return task
        matched = true
        return { ...task, stale: message.stale }
      })
      const active = patch(current.active ?? [])
      const idle_sessions = patch(current.idle_sessions ?? [])
      if (!matched) return null
      return { ...current, active, idle_sessions }
    }
    default: return null
  }
}

export function useDashboard(): DashboardState {
  const [data, setData] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [receivedAt, setReceivedAt] = useState<number | null>(null)
  const [live, setLive] = useState(false)
  const hasData = useRef(false)
  const dataRef = useRef<Snapshot | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const refresh = useCallback(() => setRefreshKey(v => v + 1), [])
  useEffect(() => {
    let disposed = false
    let eventSource: EventSource | undefined
    let pollTimer: ReturnType<typeof setInterval> | undefined
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let socket: WebSocket | undefined
    let socketTimer: ReturnType<typeof setTimeout> | undefined
    let socketRetryTimer: ReturnType<typeof setTimeout> | undefined
    let socketOpen = false
    let failures = 0
    let backoff = WS_BASE_MS
    let controller: AbortController | null = null
    let streamFailed = false
    let generation = 0
    let repairing = false
    // Deltas that arrive while a repair fetch is in flight: the HTTP snapshot
    // may predate them, so they are replayed in order on top of it.
    let pendingDeltas: WsMessage[] = []
    // A failed repair fetch is retried with exponential backoff until a
    // snapshot lands (over HTTP or the socket).
    let repairTimer: ReturnType<typeof setTimeout> | undefined
    let repairBackoff = REPAIR_BASE_MS
    const clearRepairRetry = () => {
      clearTimeout(repairTimer)
      repairTimer = undefined
      repairBackoff = REPAIR_BASE_MS
    }

    /** Stream data is newer than any in-flight HTTP snapshot: drop the
     *  pending request so a late response cannot restore older rows. */
    const invalidateFetch = () => {
      repairing = false
      pendingDeltas = []
      clearRepairRetry()
      generation++
      controller?.abort()
      controller = null
    }
    const commit = (next: Snapshot) => {
      hasData.current = true
      dataRef.current = next
      setData(next)
      setError(null)
      setReceivedAt(Date.now())
    }
    const fetchSnapshot = async () => {
      const request = ++generation
      controller?.abort()
      controller = new AbortController()
      if (!hasData.current) setLoading(true)
      try {
        const response = await fetch('/api/snapshot', { signal: controller.signal, headers: { Accept: 'application/json' } })
        if (!response.ok) throw new Error(`Snapshot request failed (${response.status})`)
        const next = await response.json() as Snapshot
        if (disposed || request !== generation) return
        let merged = next
        if (repairing) {
          for (const delta of pendingDeltas) merged = applyDelta(merged, delta) ?? merged
        }
        repairing = false
        pendingDeltas = []
        clearRepairRetry()
        commit(merged)
      } catch (cause) {
        if (disposed || (cause instanceof DOMException && cause.name === 'AbortError')) return
        setError(cause instanceof Error ? cause.message : 'Snapshot unavailable')
        if (repairing && request === generation) scheduleRepair()
      } finally {
        if (!disposed && request === generation) setLoading(false)
      }
    }
    const scheduleRepair = () => {
      if (repairTimer !== undefined) return
      const wait = repairBackoff
      repairBackoff = Math.min(REPAIR_MAX_MS, repairBackoff * 2)
      repairTimer = setTimeout(() => { repairTimer = undefined; if (!disposed && repairing) void fetchSnapshot() }, wait)
    }
    /** Start a repair unless one is already outstanding (in flight or waiting
     *  out its backoff); queued deltas replay on whichever snapshot lands. */
    const requestRepair = () => {
      if (repairing) return
      repairing = true
      void fetchSnapshot()
    }
    /** The replay queue is full: drop it and resync from a fresh socket,
     *  whose first frame is an authoritative snapshot. */
    const resync = () => {
      invalidateFetch()
      const old = socket
      if (old === undefined || !socketOpen) { requestRepair(); return }
      socket = undefined
      socketOpen = false
      old.close()
      connectWs()
    }
    /** Hold a delta for replay on the repair snapshot; false on overflow. */
    const queueDelta = (message: WsMessage) => {
      if (pendingDeltas.length >= MAX_PENDING_DELTAS) { resync(); return false }
      pendingDeltas.push(message)
      return true
    }
    const stopPolling = () => { clearInterval(pollTimer); pollTimer = undefined }
    const startPolling = () => {
      if (pollTimer !== undefined) return
      void fetchSnapshot()
      pollTimer = setInterval(() => void fetchSnapshot(), POLL_MS)
    }
    const wsOwnsStream = () => socket !== undefined && socketOpen
    const connect = () => {
      if (disposed || eventSource || wsOwnsStream()) return
      streamFailed = false
      const source = new EventSource('/api/stream')
      eventSource = source
      source.addEventListener('snapshot', ((event: MessageEvent<string>) => {
        if (eventSource !== source) return
        try {
          const next = JSON.parse(event.data) as Snapshot
          if (!disposed) { invalidateFetch(); commit(next); setLoading(false); setLive(true); stopPolling() }
        } catch { setError('Received an invalid snapshot event') }
      }) as EventListener)
      source.onopen = () => { if (!disposed && !streamFailed) { setLive(true); setError(null); stopPolling() } }
      source.onerror = () => {
        if (disposed || streamFailed) return
        streamFailed = true
        source.close()
        if (eventSource === source) eventSource = undefined
        if (wsOwnsStream()) return
        setLive(false)
        startPolling()
        if (reconnectTimer === undefined) reconnectTimer = setTimeout(() => { reconnectTimer = undefined; connect() }, RECONNECT_MS)
      }
    }
    const connectWs = () => {
      if (disposed || socket) return
      if (typeof WebSocket === 'undefined') { connect(); return }
      let counted = false
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const next = new WebSocket(`${protocol}://${window.location.host}/ws/events`)
      socket = next
      next.onopen = () => {
        if (disposed) { next.close(); return }
        socketOpen = true
        failures = 0
        backoff = WS_BASE_MS
        setLive(true)
        setError(null)
        stopPolling()
        // The socket owns the stream now: close SSE and cancel any pending
        // SSE retry so it cannot reopen beside the healthy socket.
        if (eventSource) { eventSource.close(); eventSource = undefined }
        clearTimeout(reconnectTimer)
        reconnectTimer = undefined
      }
      next.onmessage = (event: MessageEvent<string>) => {
        if (disposed || socket !== next) return
        const message = parseMessage(typeof event.data === 'string' ? event.data : '')
        if (message === null) { requestRepair(); return }
        if (message.type === 'heartbeat') return
        if (message.type === 'snapshot') { invalidateFetch(); commit(message.snapshot); setLoading(false); setLive(true); stopPolling(); return }
        const current = dataRef.current
        const nextData = current === null ? null : applyDelta(current, message)
        if (nextData === null) { if (queueDelta(message)) requestRepair(); return }
        // A repair fetch (issued because a delta could not be applied) must
        // land, so keep this delta for replay on top of it (bounded; overflow
        // resyncs); otherwise this newer delta supersedes any pending request.
        if (repairing) queueDelta(message)
        else invalidateFetch()
        setLoading(false)
        dataRef.current = nextData
        setData(nextData)
        setReceivedAt(Date.now())
      }
      const fail = () => {
        if (counted || disposed || socket !== next) return
        counted = true
        socket = undefined
        socketOpen = false
        setLive(false)
        failures += 1
        if (failures >= WS_FALLBACK_AFTER) {
          connect()
          if (socketRetryTimer === undefined) socketRetryTimer = setTimeout(() => { socketRetryTimer = undefined; connectWs() }, RECONNECT_MS)
        } else {
          backoff = Math.min(WS_MAX_MS, WS_BASE_MS * 2 ** (failures - 1))
          const wait = Math.random() * backoff
          if (socketTimer === undefined) socketTimer = setTimeout(() => { socketTimer = undefined; connectWs() }, wait)
        }
      }
      next.onclose = fail
      next.onerror = fail
    }

    void fetchSnapshot()
    connectWs()
    return () => {
      disposed = true
      generation++
      controller?.abort()
      socket?.close()
      socket = undefined
      eventSource?.close()
      stopPolling()
      clearTimeout(reconnectTimer)
      clearTimeout(socketTimer)
      clearTimeout(socketRetryTimer)
      clearTimeout(repairTimer)
    }
  }, [refreshKey])
  return { data, loading, error, receivedAt, live, refresh }
}
