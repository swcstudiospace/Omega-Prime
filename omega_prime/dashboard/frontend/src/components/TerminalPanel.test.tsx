import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, fireEvent } from '@testing-library/react'
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
  emit(frame: object) { this.listeners.get('terminal')?.({ data: JSON.stringify(frame) } as MessageEvent as Event) }
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
})
