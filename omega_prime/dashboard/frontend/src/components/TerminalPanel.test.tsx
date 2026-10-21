import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TerminalPanel } from './TerminalPanel'
class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners = new Map<string, EventListener>()
  closed = false
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor(readonly url: string) { FakeEventSource.instances.push(this) }
  addEventListener(name: string, fn: EventListener) { this.listeners.set(name, fn) }
  close() { this.closed = true }
  emit(frame: object) { act(() => this.listeners.get('terminal')?.({ data: JSON.stringify(frame) } as MessageEvent as Event)) }
}
afterEach(() => { cleanup(); FakeEventSource.instances = []; vi.unstubAllGlobals() })
describe('TerminalPanel', () => {
  it('streams plain text and cleans reconnect on task change', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const view = render(<TerminalPanel taskId="one/1" />)
    expect(FakeEventSource.instances[0].url).toBe('/api/tasks/one%2F1/terminal')
    const source = FakeEventSource.instances[0]
    source.emit({ status: 'live', text: '<img src=x>', updated_at: '2026-10-21T00:00:00Z' })
    expect(screen.getByText('<img src=x>')).toBeInTheDocument()
    act(() => { source.onerror?.(new Event('error')) })
    expect(screen.getByText('Reconnecting…')).toBeInTheDocument()
    expect(screen.getByText(/Last updated 21 Oct 2026/)).toBeInTheDocument()
    source.emit({ status: 'live', text: '<img src=x> next', updated_at: '2026-10-21T00:00:01Z' })
    expect(screen.getByText('<img src=x> next')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const old = FakeEventSource.instances[0]
    view.rerender(<TerminalPanel taskId="two" />)
    expect(old.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(2)
    view.unmount()
    expect(FakeEventSource.instances[1].closed).toBe(true)
  })
  it('keeps transport running when pausing auto-scroll and exposes status', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    render(<TerminalPanel taskId="one" />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause auto-scroll' }))
    expect(screen.getByRole('button', { name: 'Resume auto-scroll' })).toHaveAttribute('aria-pressed', 'true')
    expect(FakeEventSource.instances[0].closed).toBe(false)
  })
  it('renders local terminal failure states and copies only the displayed buffer', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const view = render(<TerminalPanel taskId="one" />)
    FakeEventSource.instances[0].emit({ status: 'live', text: 'safe text', updated_at: '2026-10-21T00:00:00Z' })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy output' }))
      await Promise.resolve()
    })
    expect(await screen.findByText('Output copied')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith('safe text')
    view.rerender(<TerminalPanel taskId="closed" />)
    FakeEventSource.instances[1].emit({ status: 'pane_closed', text: '', updated_at: '2026-10-21T00:00:01Z' })
    expect(screen.getByText('Pane closed')).toBeInTheDocument()
    expect(screen.getByText('This session has ended. Other task details remain available.')).toBeInTheDocument()
    view.rerender(<TerminalPanel taskId="missing" />)
    FakeEventSource.instances[2].emit({ status: 'no_session', text: '', updated_at: null })
    expect(screen.getByText('No session attached')).toBeInTheDocument()
    expect(screen.getByText('This task has no mapped Herdr pane. Other task details remain available.')).toBeInTheDocument()
    view.rerender(<TerminalPanel taskId="unavailable" />)
    FakeEventSource.instances[3].emit({ status: 'unavailable', text: '', updated_at: null })
    expect(screen.getByText('Herdr unavailable')).toBeInTheDocument()
    expect(screen.getByText('Terminal output cannot be read on this host. Other task details remain available.')).toBeInTheDocument()
    view.unmount()
  })
})
