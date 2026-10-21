import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { App } from './App'
import { fixture } from './test/fixtures'
vi.mock('./hooks/useDashboard', () => ({ useDashboard: () => ({ data: fixture, loading: false, error: null, receivedAt: Date.now(), live: true, refresh: vi.fn() }) }))
beforeEach(() => { window.history.replaceState(null, ''); window.location.hash = '#/overview'; sessionStorage.clear() })
afterEach(() => cleanup())
describe('Command Center hash routes', () => {
  it('opens and clears each bucket and restores initial hash', async () => {
    window.location.hash = '#/tasks?bucket=working'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Working', level: 2 })).toBeInTheDocument())
    const pageBuckets = within(screen.getAllByRole('navigation', { name: 'Task buckets' })[1]!)
    expect(pageBuckets.getByRole('link', { name: /Working.*Selected/ })).toHaveAttribute('href', '#/tasks')
    for (const bucket of ['waiting', 'pr-gate', 'finished'] as const) {
      fireEvent.click(pageBuckets.getByRole('link', { name: bucket === 'waiting' ? 'Waiting on Ove' : bucket === 'pr-gate' ? 'PR gate' : 'Finished' }))
      await waitFor(() => expect(window.location.hash).toContain(`bucket=${bucket}`))
    }
    fireEvent.click(pageBuckets.getByRole('link', { name: 'All' }))
    await waitFor(() => expect(window.location.hash).toBe('#/tasks'))
    act(() => { window.location.hash = '#/tasks?bucket=unknown' })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Unknown bucket. Showing All tasks.'))
  })
  it('preserves bucket origin through task detail refresh Back and Forward', async () => {
    window.location.hash = '#/tasks?bucket=working'
    const first = render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Working', level: 2 })).toBeInTheDocument())
    fireEvent.click(within(screen.getByRole('main')).getByRole('link', { name: /00007 - Compile release/ }))
    await waitFor(() => expect(screen.getByRole('heading', { name: /Compile release/, level: 2 })).toBeInTheDocument())
    expect(screen.getByRole('link', { name: '← Back to Working' })).toHaveAttribute('href', '#/tasks?bucket=working')
    expect(window.history.state).toMatchObject({ taskOrigin: 'working', taskOriginTaskId: 'worker/1' })
    first.unmount()
    render(<App />)
    expect(screen.getByRole('link', { name: '← Back to Working' })).toBeInTheDocument()
    act(() => window.history.back())
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Working', level: 2 })).toBeInTheDocument())
    act(() => window.history.forward())
    await waitFor(() => expect(screen.getByRole('heading', { name: /Compile release/, level: 2 })).toBeInTheDocument())
  })
})
