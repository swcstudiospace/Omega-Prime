import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Repositories } from './Repositories'
import { fixture } from '../test/fixtures'
describe('Repositories', () => {
  it('searches available inventory and preserves safe GitHub links', () => { render(<Repositories snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />); expect(screen.getByText('swcstudiospace/console')).toBeInTheDocument(); fireEvent.change(screen.getByLabelText('Search repositories'), { target: { value: 'absent' } }); expect(screen.getByText('No repositories match this search.')).toBeInTheDocument() })
  it('shows only genuine section failures with their reason and original freshness', () => {
    const partial = { ...fixture, sources: { ...fixture.sources, github: { ...fixture.sources?.github, command_center: { ...fixture.sources?.github?.command_center, meta: { partial: true, fetched_at: '2026-01-03T00:00:00Z', sections: { recent_merges: { partial: true, stale: true, reason: 'rate limit', fetched_at: '2026-01-01T00:00:00Z' } } } } } } }
    const { rerender } = render(<Repositories snapshot={partial} data={partial} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('recent_merges: rate limit')
    expect(screen.getByRole('status')).toHaveTextContent('Showing last successful data · stale')
    expect(screen.getByRole('status')).toHaveTextContent('Previous successful fetch:')
    expect(screen.queryByText(/Partial source data/)).toBeInTheDocument()
    rerender(<Repositories snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)
    expect(screen.queryByText(/Partial source data/)).not.toBeInTheDocument()
  })
})
