import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Tasks } from './Tasks'
import { fixture } from '../test/fixtures'
afterEach(cleanup)
describe('Tasks', () => {
  it('filters by title without changing canonical numbering', () => { render(<Tasks snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="all" notice={null} onTask={vi.fn()} />); fireEvent.change(screen.getByLabelText('Search work'), { target: { value: 'Approve' } }); expect(screen.getByText('Approve deployment')).toBeInTheDocument(); expect(screen.queryByText('00001')).not.toBeInTheDocument() })
  it('shows a stale badge with its reason only on stale rows', () => {
    const staleSnapshot = { ...fixture, active: [{ ...fixture.active![0]!, stale: { reason: 'pid_dead', since: '2026-01-01T00:05:00Z' } }] }
    render(<Tasks snapshot={staleSnapshot} data={staleSnapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="all" notice={null} onTask={vi.fn()} />)
    const badge = screen.getByText('STALE · pid_dead')
    expect(badge).toHaveClass('stale-pill')
    expect(badge).toHaveAttribute('title')
    expect(badge.getAttribute('title')).toContain('2026')
  })
  it('renders no stale badge for fresh tasks', () => {
    render(<Tasks snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="all" notice={null} onTask={vi.fn()} />)
    expect(screen.queryByText(/STALE/, { selector: '.stale-pill' })).not.toBeInTheDocument()
  })
})
