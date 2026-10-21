import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Snapshot } from '../types'
import { App } from '../App'
import { fixture } from '../test/fixtures'
class PassiveEventSource {
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor(_url: string) {}
  addEventListener(_name: string, _listener: EventListenerOrEventListenerObject) {}
  close() {}
}
const state = vi.hoisted(() => ({ data: null as Snapshot | null, loading: false }))
vi.mock('../hooks/useDashboard', () => ({ useDashboard: () => ({ data: state.data, loading: state.loading, error: null, receivedAt: state.data ? Date.now() : null, live: true, refresh: vi.fn() }) }))
beforeEach(() => { vi.stubGlobal('EventSource', PassiveEventSource); vi.stubGlobal('WebSocket', undefined); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('terminal session unavailable'))); state.data = fixture; state.loading = false; window.history.replaceState(null, ''); window.location.hash = '#/overview'; sessionStorage.clear() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
describe('Task detail routes', () => {
  it('opens numbered task detail with recorded links PR evidence wiring and events', async () => {
    window.location.hash = '#/tasks/worker%2F1'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /Compile release/, level: 2 })).toBeInTheDocument())
    expect(screen.getByText('/worktrees/release')).toBeInTheDocument()
    expect(screen.getByText('codex')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Linear OPS-1' })).toHaveAttribute('target', '_blank')
    expect(screen.queryByRole('link', { name: 'Unsafe link' })).not.toBeInTheDocument()
    expect(screen.getByText('CI: success · Greptile 4/5 · from #12')).toBeInTheDocument()
    expect(screen.getByText(/Task resumed/)).toBeInTheDocument()
    expect(screen.getByText('Read only · redacted · last 200 lines')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '← Back to All tasks' }))
    await waitFor(() => expect(window.location.hash).toBe('#/tasks'))
  })
  it('shows recovery only for an unknown task after snapshot data is available', () => {
    window.location.hash = '#/tasks/not-known'
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Task not found', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View tasks' })).toHaveAttribute('href', '#/tasks')
  })
  it('resolves Idle and finished identities with their recorded evidence', async () => {
    window.location.hash = '#/tasks/finished%2F3'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /Publish report/, level: 2 })).toBeInTheDocument())
    expect(screen.getByText('ok')).toBeInTheDocument()
    expect(screen.getByText('receipt-3')).toBeInTheDocument()
    expect(screen.getByText('/worktrees/report')).toBeInTheDocument()
    act(() => { window.location.hash = '#/tasks/idle%2F2' })
    await waitFor(() => expect(screen.getByRole('heading', { name: /Quiet sandbox/, level: 2 })).toBeInTheDocument())
  })
  it('does not show not-found until the initial snapshot has loaded', () => {
    state.data = null; state.loading = true; window.location.hash = '#/tasks/missing'
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Loading task details…', level: 1 })).toBeInTheDocument()
    expect(screen.queryByText('Task not found')).not.toBeInTheDocument()
  })
  it('shows a stale badge in the detail heading only for stale tasks', async () => {
    state.data = { ...fixture, active: [{ ...fixture.active![0]!, stale: { reason: 'pid_dead', since: '2026-01-01T00:05:00Z' } }] }
    window.location.hash = '#/tasks/worker%2F1'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /Compile release/, level: 2 })).toBeInTheDocument())
    const badge = document.querySelector('.stale-pill')
    expect(badge?.textContent).toBe('STALE · pid_dead')
    expect(badge?.getAttribute('title')).toContain('2026')
    cleanup()
    state.data = fixture
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /Compile release/, level: 2 })).toBeInTheDocument())
    expect(document.querySelector('.stale-pill')).toBeNull()
  })
})
