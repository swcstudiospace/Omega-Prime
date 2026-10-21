import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CiCd } from './CiCd'
import { fixture } from '../test/fixtures'
describe('CI/CD', () => {
  it('shows PR checks, default-branch evidence and labels unsupported wiring unknown', () => { render(<CiCd snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />); expect(screen.getByText('build')).toBeInTheDocument(); expect(screen.getAllByText(/Unknown · no unique branch\/SHA corroboration/)).toHaveLength(1); expect(screen.getByRole('heading', { name: 'main' })).toBeInTheDocument() })
  it('shows recent_merges failure with original successful section freshness', () => {
    const snapshot = structuredClone(fixture)
    const oldSuccess = '2026-10-18T11:00:00Z'
    const aggregate = '2026-10-20T15:00:00Z'
    snapshot.sources!.github!.status = 'degraded'
    snapshot.sources!.github!.command_center!.meta = {
      fetched_at: aggregate, last_attempt_at: aggregate, partial: true, stale: true, reason: 'too_large',
      sections: {
        ci_cd: { fetched_at: aggregate, partial: false, stale: false, reason: null },
        repositories: { fetched_at: aggregate, partial: false, stale: false, reason: null },
        recent_merges: { fetched_at: oldSuccess, partial: true, stale: true, reason: 'too_large' },
      },
    }
    const receivedAt = Date.parse('2026-10-21T10:00:00Z')
    const view = render(<CiCd snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={receivedAt} live refresh={vi.fn()} />)
    const notice = screen.getByText(/Partial source data · recent_merges: too_large\. Showing last successful data · stale\./)
    expect(notice).toHaveTextContent(/18 Oct 2026/)
    expect(notice).not.toHaveTextContent(/21 Oct 2026/)
    expect(screen.getByRole('heading', { name: 'Fix safe view' })).toBeInTheDocument()
    snapshot.sources!.github!.status = 'ok'
    snapshot.sources!.github!.command_center!.meta = {
      fetched_at: aggregate, partial: false, stale: false,
      sections: {
        ci_cd: { fetched_at: aggregate, partial: false, stale: false, reason: null },
        repositories: { fetched_at: aggregate, partial: false, stale: false, reason: null },
        recent_merges: { fetched_at: aggregate, partial: false, stale: false, reason: null },
      },
    }
    view.rerender(<CiCd snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={receivedAt} live refresh={vi.fn()} />)
    expect(screen.queryByText(/Partial source data/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fix safe view' })).toBeInTheDocument()
  })
})
