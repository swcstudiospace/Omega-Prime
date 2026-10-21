import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TerminalPanel } from './TerminalPanel'
const terminalState = vi.hoisted(() => ({ instances: [] as Array<{ options: unknown; ops: string[]; dataHandler: ((data: string) => void) | null; disposed: boolean; addons: unknown[] }> }))
vi.mock('@xterm/xterm', () => {
  class FakeTerminal {
    options: unknown
    ops: string[] = []
    dataHandler: ((data: string) => void) | null = null
    disposed = false
    constructor(options: unknown) { this.options = options; terminalState.instances.push(this) }
    addons: unknown[] = []
    loadAddon(addon: unknown) { this.addons.push(addon) }
    open() { this.ops.push('open') }
    write(text: string) { this.ops.push(`write:${text}`) }
    reset() { this.ops.push('reset') }
    onData(handler: (data: string) => void) { this.dataHandler = handler; return { dispose: () => {} } }
    dispose() { this.disposed = true }
  }
  return { default: FakeTerminal, Terminal: FakeTerminal }
})
const fitState = vi.hoisted(() => ({ fits: 0 }))
vi.mock('@xterm/addon-fit', () => {
  class FakeFitAddon {
    activate() {}
    dispose() {}
    fit() { fitState.fits += 1 }
  }
  return { FitAddon: FakeFitAddon }
})
const terminalInstances = () => terminalState.instances
class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners: Record<string, EventListener> = {}
  closed = false
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor(readonly url: string) { FakeEventSource.instances.push(this) }
  addEventListener(name: string, fn: EventListener) { this.listeners[name] = fn }
  close() { this.closed = true }
  emit(frame: object) { act(() => this.listeners.terminal?.({ data: JSON.stringify(frame) } as MessageEvent as Event)) }
}
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event?: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  sent: unknown[] = []
  closed = false
  constructor(readonly url: string) { FakeWebSocket.instances.push(this) }
  send(payload: string) { this.sent.push(JSON.parse(payload)) }
  close() { this.closed = true }
  open() { act(() => this.onopen?.()) }
  emit(message: object) { act(() => this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)) }
  drop(code?: number) { act(() => { this.onclose?.(code === undefined ? undefined : ({ code } as CloseEvent)) }) }
}
const FAKE_TOKEN = 'fixed-fake-token'
const stubSession = (session: { token: string; readonly: boolean } | null, rejectAll = false) => {
  vi.stubGlobal('fetch', rejectAll
    ? vi.fn().mockRejectedValue(new Error('offline'))
    : vi.fn(async (url: unknown) => {
      if (url === '/api/session' && session !== null) return { ok: true, json: async () => session }
      return { ok: false, status: 404 }
    }))
}
const flush = async () => { await act(async () => {}) }
const badge = (text: string) => screen.getByText(text, { selector: '.terminal-badge' })
afterEach(() => {
  cleanup()
  FakeEventSource.instances = []
  FakeWebSocket.instances = []
  terminalInstances().length = 0
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
describe('TerminalPanel', () => {
  it('streams plain text and cleans reconnect on task change', async () => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession(null, true)
    const view = render(<TerminalPanel taskId="one/1" />)
    await flush()
    expect(FakeEventSource.instances[0]!.url).toBe('/api/tasks/one%2F1/terminal')
    const source = FakeEventSource.instances[0]!
    source.emit({ status: 'live', text: '<img src=x>', updated_at: '2026-10-21T00:00:00Z' })
    expect(screen.getByText('<img src=x>')).toBeInTheDocument()
    act(() => { source.onerror?.(new Event('error')) })
    expect(screen.getByText('Reconnecting…')).toBeInTheDocument()
    expect(screen.getByText(/Last updated 21 Oct 2026/)).toBeInTheDocument()
    source.emit({ status: 'live', text: '<img src=x> next', updated_at: '2026-10-21T00:00:01Z' })
    expect(screen.getByText('<img src=x> next')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const old = FakeEventSource.instances[0]!
    view.rerender(<TerminalPanel taskId="two" />)
    await flush()
    expect(old.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(2)
    view.unmount()
    expect(FakeEventSource.instances[1]!.closed).toBe(true)
  })
  it('keeps transport running when pausing auto-scroll and exposes status', async () => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession(null, true)
    render(<TerminalPanel taskId="one" />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Pause auto-scroll' }))
    expect(screen.getByRole('button', { name: 'Resume auto-scroll' })).toHaveAttribute('aria-pressed', 'true')
    expect(FakeEventSource.instances[0]!.closed).toBe(false)
  })
  it('renders local terminal failure states and copies only the displayed buffer', async () => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession(null, true)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const view = render(<TerminalPanel taskId="one" />)
    await flush()
    FakeEventSource.instances[0]!.emit({ status: 'live', text: 'safe text', updated_at: '2026-10-21T00:00:00Z' })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy output' }))
      await Promise.resolve()
    })
    expect(await screen.findByText('Output copied')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith('safe text')
    view.rerender(<TerminalPanel taskId="closed" />)
    await flush()
    FakeEventSource.instances[1]!.emit({ status: 'pane_closed', text: '', updated_at: '2026-10-21T00:00:01Z' })
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
    expect(screen.getByText('This session has ended. Other task details remain available.')).toBeInTheDocument()
    view.rerender(<TerminalPanel taskId="missing" />)
    await flush()
    FakeEventSource.instances[2]!.emit({ status: 'no_session', text: '', updated_at: null })
    expect(screen.getByText('No session attached')).toBeInTheDocument()
    expect(screen.getByText('This task has no mapped Herdr pane. Other task details remain available.')).toBeInTheDocument()
    view.rerender(<TerminalPanel taskId="unavailable" />)
    await flush()
    FakeEventSource.instances[3]!.emit({ status: 'unavailable', text: '', updated_at: null })
    expect(screen.getByText('Herdr unavailable')).toBeInTheDocument()
    expect(screen.getByText('Terminal output cannot be read on this host. Other task details remain available.')).toBeInTheDocument()
    view.unmount()
  })
  it('derives the socket url, authenticates first, and renders full-screen frames', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    const view = render(<TerminalPanel taskId="one/1" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    expect(socket.url).toBe(`ws://${window.location.host}/ws/tasks/one%2F1/terminal`)
    socket.open()
    expect(socket.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'rw' })
    const terminal = terminalInstances()[0]!
    expect(terminal.options).toMatchObject({ convertEol: true, rows: 24, cols: 80 })
    socket.emit({ type: 'frame', text: '\x1b[31mready\x1b[0m', updated_at: '2026-10-21T00:00:00Z' })
    expect(terminal.ops).toEqual(['open', 'reset', 'write:\x1b[31mready\x1b[0m'])
    expect(screen.getByText(/Last updated 21 Oct 2026/)).toBeInTheDocument()
    expect(badge('Attached')).toBeInTheDocument()
    terminal.dataHandler!('ls')
    expect(socket.sent).toContainEqual({ type: 'input', text: 'ls' })
    terminal.dataHandler!('\r')
    expect(socket.sent).toContainEqual({ type: 'keys', keys: ['enter'] })
    view.unmount()
    expect(socket.closed).toBe(true)
    expect(terminal.disposed).toBe(true)
  })
  it('stops all input and sends mode ro when the read-only toggle is on', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Read-only' }))
    await flush()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    expect(socket.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'ro' })
    expect(badge('Read-only')).toBeInTheDocument()
    terminalInstances()[0]!.dataHandler!('ls')
    expect(socket.sent).toHaveLength(1)
  })
  it('reconnects and re-authenticates when the read-only toggle changes after auth', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const first = FakeWebSocket.instances[0]!
    first.open()
    expect(first.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'rw' })
    fireEvent.click(screen.getByRole('button', { name: 'Read-only' }))
    await flush()
    expect(first.closed).toBe(true)
    const readonly = FakeWebSocket.instances.at(-1)!
    expect(readonly).not.toBe(first)
    readonly.open()
    expect(readonly.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'ro' })
    fireEvent.click(screen.getByRole('button', { name: 'Read-only' }))
    await flush()
    expect(readonly.closed).toBe(true)
    const writable = FakeWebSocket.instances.at(-1)!
    expect(writable).not.toBe(readonly)
    writable.open()
    expect(writable.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'rw' })
    expect(badge('Attached')).toBeInTheDocument()
    terminalInstances().at(-1)!.dataHandler!('ls')
    expect(writable.sent).toContainEqual({ type: 'input', text: 'ls' })
  })
  it('clears an error badge when the pane recovers', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    socket.emit({ type: 'status', status: 'unavailable' })
    expect(badge('Herdr unavailable')).toBeInTheDocument()
    socket.emit({ type: 'status', status: 'live' })
    expect(badge('Attached')).toBeInTheDocument()
    socket.emit({ type: 'status', status: 'no_session' })
    expect(badge('No session attached')).toBeInTheDocument()
    socket.emit({ type: 'frame', text: 'back', updated_at: '2026-10-21T00:00:00Z' })
    expect(badge('Attached')).toBeInTheDocument()
  })
  it('fits the terminal to its container on mount', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    const before = fitState.fits
    render(<TerminalPanel taskId="one" />)
    await flush()
    expect(terminalInstances().at(-1)!.addons).toHaveLength(1)
    expect(fitState.fits).toBeGreaterThan(before)
  })
  it('keeps the fallback view following new output until paused', async () => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession(null, true)
    render(<TerminalPanel taskId="one" />)
    await flush()
    const region = screen.getByRole('region', { name: 'Read-only terminal output' })
    Object.defineProperty(region, 'scrollHeight', { configurable: true, value: 500 })
    Object.defineProperty(region, 'clientHeight', { configurable: true, value: 100 })
    FakeEventSource.instances[0]!.emit({ status: 'live', text: 'one', updated_at: '2026-10-21T00:00:00Z' })
    expect(region.scrollTop).toBe(500)
    fireEvent.click(screen.getByRole('button', { name: 'Pause auto-scroll' }))
    region.scrollTop = 10
    FakeEventSource.instances[0]!.emit({ status: 'live', text: 'one\ntwo', updated_at: '2026-10-21T00:00:01Z' })
    expect(region.scrollTop).toBe(10)
    fireEvent.click(screen.getByRole('button', { name: 'Resume auto-scroll' }))
    expect(region.scrollTop).toBe(500)
  })
  it('forces mode ro without input when the session is readonly', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: true })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    expect(socket.sent[0]).toEqual({ type: 'auth', token: FAKE_TOKEN, mode: 'ro' })
    expect(badge('Read-only')).toBeInTheDocument()
    terminalInstances()[0]!.dataHandler!('\r')
    expect(socket.sent).toHaveLength(1)
  })
  it('detaches and reattaches with a fresh socket', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    fireEvent.click(screen.getByRole('button', { name: 'Detach' }))
    expect(socket.closed).toBe(true)
    expect(badge('Detached')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Attach' }))
    await flush()
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
  it('shows pane closed and never auto-reconnects it', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    socket.emit({ type: 'status', status: 'pane_closed' })
    expect(badge('Pane closed')).toBeInTheDocument()
    expect(socket.closed).toBe(true)
    await act(async () => { vi.advanceTimersByTime(90_000) })
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
  it('shows the no-session status with the task note in both views', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    const interactive = render(<TerminalPanel taskId="one" note="waiting for creds" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    socket.emit({ type: 'status', status: 'no_session' })
    expect(badge('No session attached')).toBeInTheDocument()
    expect(screen.getByText('No session attached — last note: waiting for creds')).toBeInTheDocument()
    interactive.unmount()
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession(null, true)
    render(<TerminalPanel taskId="one" note="waiting for creds" />)
    await flush()
    FakeEventSource.instances[0]!.emit({ status: 'no_session', text: '', updated_at: null })
    expect(screen.getByText(/This task has no mapped Herdr pane\. Other task details remain available\. Last note: waiting for creds/)).toBeInTheDocument()
  })
  it('falls back to the read-only SSE view when no websocket exists', async () => {
    vi.stubGlobal('WebSocket', undefined)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one/1" />)
    await flush()
    expect(screen.getByText('Read only · redacted · last 200 lines')).toBeInTheDocument()
    expect(FakeEventSource.instances[0]!.url).toBe('/api/tasks/one%2F1/terminal')
  })
  it('serves the sse view on socket failure and retries the websocket on the 30s cadence', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('EventSource', FakeEventSource)
    stubSession({ token: FAKE_TOKEN, readonly: false })
    render(<TerminalPanel taskId="one" />)
    await flush()
    const socket = FakeWebSocket.instances[0]!
    socket.open()
    socket.drop()
    expect(screen.getByText('Read only · redacted · last 200 lines')).toBeInTheDocument()
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(30_000) })
    await flush()
    expect(FakeWebSocket.instances).toHaveLength(2)
    FakeWebSocket.instances[1]!.open()
    expect(screen.queryByText('Read only · redacted · last 200 lines')).not.toBeInTheDocument()
    expect(FakeEventSource.instances[0]!.closed).toBe(true)
  })
  it('refetches the session and reconnects with the new token after a 4401 rejection', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('EventSource', FakeEventSource)
    let token = 'token-before-restart'
    vi.stubGlobal('fetch', vi.fn(async (url: unknown) => url === '/api/session'
      ? { ok: true, json: async () => ({ token, readonly: false }) }
      : { ok: false, status: 404 }))
    render(<TerminalPanel taskId="one" />)
    await flush()
    const first = FakeWebSocket.instances[0]!
    first.open()
    expect(first.sent[0]).toMatchObject({ type: 'auth', token: 'token-before-restart' })
    token = 'token-after-restart'
    first.drop(4401)
    await flush()
    expect(FakeWebSocket.instances).toHaveLength(2)
    FakeWebSocket.instances[1]!.open()
    expect(FakeWebSocket.instances[1]!.sent[0]).toMatchObject({ type: 'auth', token: 'token-after-restart' })
    expect(FakeEventSource.instances).toHaveLength(0)
  })
})
