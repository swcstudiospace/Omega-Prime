import { useCallback, useEffect, useRef, useState } from 'react'
import type { Snapshot } from '../types'

const POLL_MS = 5_000
const RECONNECT_MS = 30_000

export type DashboardState = { data: Snapshot | null; loading: boolean; error: string | null; receivedAt: number | null; live: boolean; refresh: () => void }
export function useDashboard(): DashboardState {
  const [data, setData] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [receivedAt, setReceivedAt] = useState<number | null>(null)
  const [live, setLive] = useState(false)
  const hasData = useRef(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const refresh = useCallback(() => setRefreshKey(v => v + 1), [])
  useEffect(() => {
    let disposed = false
    let eventSource: EventSource | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let controller: AbortController | null = null
    let streamFailed = false
    let generation = 0

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
        hasData.current = true
        setData(next)
        setError(null)
        setReceivedAt(Date.now())
      } catch (cause) {
        if (disposed || (cause instanceof DOMException && cause.name === 'AbortError')) return
        setError(cause instanceof Error ? cause.message : 'Snapshot unavailable')
      } finally {
        if (!disposed && request === generation) setLoading(false)
      }
    }
    const stopPolling = () => { if (pollTimer !== null) clearInterval(pollTimer); pollTimer = null }
    const startPolling = () => {
      if (pollTimer !== null) return
      void fetchSnapshot()
      pollTimer = setInterval(() => void fetchSnapshot(), POLL_MS)
    }
    const connect = () => {
      if (disposed || eventSource) return
      streamFailed = false
      const source = new EventSource('/api/stream')
      eventSource = source
      source.addEventListener('snapshot', ((event: MessageEvent<string>) => {
        try {
          const next = JSON.parse(event.data) as Snapshot
          if (!disposed) { hasData.current = true; setData(next); setError(null); setReceivedAt(Date.now()); setLoading(false); setLive(true); stopPolling() }
        } catch { setError('Received an invalid snapshot event') }
      }) as EventListener)
      source.onopen = () => { if (!disposed && !streamFailed) { setLive(true); setError(null); stopPolling() } }
      source.onerror = () => {
        if (disposed || streamFailed) return
        streamFailed = true
        setLive(false)
        source.close()
        if (eventSource === source) eventSource = null
        startPolling()
        if (reconnectTimer === null) reconnectTimer = setTimeout(() => { reconnectTimer = null; connect() }, RECONNECT_MS)
      }
    }

    void fetchSnapshot()
    connect()
    return () => {
      disposed = true
      generation++
      controller?.abort()
      eventSource?.close()
      stopPolling()
      clearTimeout(reconnectTimer ?? undefined)
    }
  }, [refreshKey])
  return { data, loading, error, receivedAt, live, refresh }
}
