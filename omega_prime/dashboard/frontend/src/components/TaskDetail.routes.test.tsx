import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Snapshot } from '../types'
import { App } from '../App'
import { fixture } from '../test/fixtures'
const state = vi.hoisted(() => ({ data: null as Snapshot | null, loading: false }))
vi.mock('../hooks/useDashboard', () => ({ useDashboard: () => ({ data: state.data, loading: state.loading, error: null, receivedAt: state.data ? Date.now() : null, live: true, refresh: vi.fn() }) }))
beforeEach(() => { state.data = fixture; state.loading = false; window.history.replaceState(null, ''); window.location.hash = '#/overview'; sessionStorage.clear() })
afterEach(() => cleanup())
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
    expect(screen.getByText('Terminal streaming not connected')).toBeInTheDocument()
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
    expect(screen.getByText('Loading task details…')).toBeInTheDocument()
    expect(screen.queryByText('Task not found')).not.toBeInTheDocument()
  })
})
