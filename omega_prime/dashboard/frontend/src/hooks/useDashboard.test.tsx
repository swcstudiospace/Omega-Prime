import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useDashboard } from './useDashboard'
import { fixture } from '../test/fixtures'
class FakeEventSource { static latest: FakeEventSource; listeners: Record<string, EventListener> = {}; onopen: (() => void) | null = null; onerror: (() => void) | null = null; closed = false; constructor(_url: string) { FakeEventSource.latest = this } addEventListener(name: string, callback: EventListener) { this.listeners[name] = callback } close() { this.closed = true } emit(data: unknown) { this.listeners.snapshot?.(new MessageEvent('snapshot', { data: JSON.stringify(data) })) } }
class RecordingEventSource extends FakeEventSource { static made: RecordingEventSource[] = []; constructor(url: string) { super(url); RecordingEventSource.made.push(this) } }
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static get latest(): FakeWebSocket { return FakeWebSocket.instances[FakeWebSocket.instances.length - 1] }
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(readonly url: string) { FakeWebSocket.instances.push(this) }
  close() { this.closed = true }
  open() { act(() => this.onopen?.()) }
  emit(message: unknown) { act(() => this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)) }
  fail() { act(() => { this.onerror?.(); this.onclose?.() }) }
}
const stubFetchSnapshot = () => vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => fixture }))
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); FakeWebSocket.instances = []; RecordingEventSource.made = [] })
describe('useDashboard transport', () => {
  it('fetches once and consumes the named snapshot SSE event', async () => { vi.stubGlobal('WebSocket', undefined); vi.stubGlobal('EventSource', FakeEventSource); stubFetchSnapshot(); const { result, unmount } = renderHook(() => useDashboard()); await waitFor(() => expect(result.current.data).toEqual(fixture)); act(() => FakeEventSource.latest.emit({ ...fixture, generated_at: 'newer' })); expect(result.current.data?.generated_at).toBe('newer'); unmount(); expect(FakeEventSource.latest.closed).toBe(true) })
  it('starts a single polling interval on stream failure and cleans it on unmount', async () => { vi.useFakeTimers(); vi.stubGlobal('WebSocket', undefined); vi.stubGlobal('EventSource', FakeEventSource); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline'))); const setIntervalSpy = vi.spyOn(globalThis, 'setInterval'); const { unmount } = renderHook(() => useDashboard()); act(() => FakeEventSource.latest.onerror?.()); expect(setIntervalSpy).toHaveBeenCalledTimes(1); unmount(); expect(FakeEventSource.latest.closed).toBe(true) })
})
describe('useDashboard websocket tier', () => {
  it('prefers /ws/events and renders the snapshot message live', async () => {
    vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', FakeWebSocket); stubFetchSnapshot()
    const { result, unmount } = renderHook(() => useDashboard())
    const socket = FakeWebSocket.latest
    expect(socket.url).toBe(`ws://${window.location.host}/ws/events`)
    socket.open()
    socket.emit({ type: 'snapshot', snapshot: fixture })
    await waitFor(() => expect(result.current.data).toEqual(fixture))
    expect(result.current.live).toBe(true)
    expect(RecordingEventSource.made).toHaveLength(0)
    socket.emit({ type: 'heartbeat' })
    expect(result.current.data).toEqual(fixture)
    unmount()
    expect(socket.closed).toBe(true)
  })
  it('applies every decision-9 delta immutably', async () => {
    vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', FakeWebSocket); stubFetchSnapshot()
    const { result, unmount } = renderHook(() => useDashboard())
    const socket = FakeWebSocket.latest
    socket.open()
    socket.emit({ type: 'snapshot', snapshot: fixture })
    await waitFor(() => expect(result.current.data).not.toBeNull())
    const before = result.current.data
    socket.emit({ type: 'task_changed', task: { ...fixture.active![0]!, title: 'Compile release v2' } })
    expect(result.current.data?.active![0]!.title).toBe('Compile release v2')
    expect(result.current.data).not.toBe(before)
    expect(before?.active![0]!.title).toBe('Compile release')
    const finishedTask = { ...fixture.active![0]!, state: 'done' }
    socket.emit({ type: 'task_finished', task: finishedTask })
    expect(result.current.data?.active).toHaveLength(0)
    // Matches normalize_tasks: undated (just finished) rows lead the list.
    expect(result.current.data?.finished?.[0]?.id).toBe('worker/1')
    socket.emit({ type: 'waiting_added', item: { id: 'wait-2', ask: 'Rotate keys' } })
    expect(result.current.data?.waiting_on_ove).toHaveLength(2)
    socket.emit({ type: 'waiting_added', item: { id: 'wait-2', ask: 'Rotate keys twice' } })
    expect(result.current.data?.waiting_on_ove).toHaveLength(2)
    expect(result.current.data?.waiting_on_ove?.find(item => item.id === 'wait-2')?.ask).toBe('Rotate keys')
    socket.emit({ type: 'waiting_cleared', id: 'wait-1' })
    expect(result.current.data?.waiting_on_ove?.map(item => item.id)).toEqual(['wait-2'])
    const stale = { reason: 'pid_dead', since: '2026-01-01T00:06:00Z' }
    socket.emit({ type: 'stale_changed', id: 'idle/2', stale })
    expect(result.current.data?.idle_sessions![0]!.stale).toEqual(stale)
    socket.emit({ type: 'stale_changed', id: 'idle/2', stale: null })
    expect(result.current.data?.idle_sessions![0]!.stale).toBeNull()
    unmount()
  })
  it('a late HTTP snapshot cannot overwrite newer stream data', async () => {
    vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', FakeWebSocket)
    let resolveFetch: (value: unknown) => void = () => {}
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn((_url: unknown, init?: { signal?: AbortSignal }) => {
      if (init?.signal) signals.push(init.signal)
      return new Promise(resolve => { resolveFetch = resolve })
    }))
    const { result, unmount } = renderHook(() => useDashboard())
    const socket = FakeWebSocket.latest
    socket.open()
    const newer = { ...fixture, generated_at: 'stream-newer' }
    socket.emit({ type: 'snapshot', snapshot: newer })
    await waitFor(() => expect(result.current.data?.generated_at).toBe('stream-newer'))
    socket.emit({ type: 'task_changed', task: { ...fixture.active![0]!, title: 'Live title' } })
    expect(signals[0]?.aborted).toBe(true)
    await act(async () => { resolveFetch({ ok: true, json: async () => ({ ...fixture, generated_at: 'http-older' }) }) })
    expect(result.current.data?.generated_at).toBe('stream-newer')
    expect(result.current.data?.active![0]!.title).toBe('Live title')
    expect(result.current.loading).toBe(false)
    unmount()
  })
  it('fetches /api/snapshot for a message it cannot apply', async () => {
    vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', FakeWebSocket); stubFetchSnapshot()
    const { unmount } = renderHook(() => useDashboard())
    const socket = FakeWebSocket.latest
    socket.open()
    socket.emit({ type: 'snapshot', snapshot: fixture })
    const fetchMock = vi.mocked(fetch)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    socket.emit({ type: 'stale_changed', id: 'no-such-task', stale: null })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    socket.emit({ type: 'mystery', payload: 1 })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    socket.emit('not-json-at-all')
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4))
    unmount()
  })
  it('backs off with full jitter and falls back to SSE after three failures', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', FakeWebSocket)
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval')
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const { unmount } = renderHook(() => useDashboard())
    act(() => { vi.advanceTimersByTime(0) })
    expect(FakeWebSocket.instances).toHaveLength(1)
    FakeWebSocket.instances[0]!.fail()
    act(() => { vi.advanceTimersByTime(499) })
    expect(FakeWebSocket.instances).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(1) })
    expect(FakeWebSocket.instances).toHaveLength(2)
    FakeWebSocket.instances[1]!.fail()
    act(() => { vi.advanceTimersByTime(999) })
    expect(FakeWebSocket.instances).toHaveLength(2)
    act(() => { vi.advanceTimersByTime(1) })
    expect(FakeWebSocket.instances).toHaveLength(3)
    FakeWebSocket.instances[2]!.fail()
    expect(RecordingEventSource.made).toHaveLength(1)
    RecordingEventSource.made[0]!.onerror?.()
    const fetchCallsAfterFallback = vi.mocked(fetch).mock.calls.length
    act(() => { vi.advanceTimersByTime(5_000) })
    expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(fetchCallsAfterFallback)
    act(() => { vi.advanceTimersByTime(30_000) })
    expect(FakeWebSocket.instances).toHaveLength(4)
    RecordingEventSource.made[0]!.onerror?.()
    const pollsBeforeOpen = vi.mocked(fetch).mock.calls.length
    FakeWebSocket.instances[3]!.open()
    expect(RecordingEventSource.made[0]!.closed).toBe(true)
    act(() => { vi.advanceTimersByTime(12_000) })
    expect(vi.mocked(fetch).mock.calls.length).toBe(pollsBeforeOpen)
    FakeWebSocket.instances[3]!.fail()
    act(() => { vi.advanceTimersByTime(500) })
    expect(FakeWebSocket.instances).toHaveLength(5)
    unmount()
    expect(setIntervalSpy).toHaveBeenCalled()
  })
})

describe('delta ordering mirrors normalize_tasks', () => {
  it('sorts active, waiting and finished like the server', async () => {
    const { sortActive, sortWaiting, sortFinished } = await import('./useDashboard')
    const task = (id: string, state: string) => ({ ...fixture.active![0]!, id, state })
    expect(sortActive([task('b', 'idle'), task('c', 'working'), task('a', 'working'), task('z', 'blocked')]).map(t => t.id)).toEqual(['z', 'a', 'c', 'b'])
    expect(sortWaiting([{ id: 'x', ask: '' }, { id: 'old', ask: '', since: '2026-01-01T00:00:00Z' }, { id: 'new', ask: '', since: '2026-01-02T00:00:00Z' }]).map(i => i.id)).toEqual(['new', 'old', 'x'])
    const done = (id: string, finished_at: string | null) => ({ id, title: id, result: 'ok', finished_at })
    expect(sortFinished([done('old', '2026-01-01T00:00:00Z'), done('undated', null), done('new', '2026-01-02T00:00:00Z')]).map(i => i.id)).toEqual(['undated', 'new', 'old'])
    expect(sortFinished(Array.from({ length: 60 }, (_, n) => done(`f${n}`, null)))).toHaveLength(50)
  })
})
