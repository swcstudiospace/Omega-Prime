import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CiCd } from './CiCd'
import { fixture } from '../test/fixtures'
import type { CiRow, Snapshot } from '../types'

describe('CI/CD Greptile review wording', () => {
  it('renders merged PR score, no PR, unavailable and unreported reviewed SHA', () => {
    const mergedReview = { score: 0, reviewed_sha: 'pr-head-12', source: 'merged_pr', evidence_source: 'review', pr_number: 12, url: 'https://github.com/swcstudiospace/console/pull/12', matches_head: false } as NonNullable<CiRow['greptile_review']> & { pr_number: number; evidence_source: string }
    const bodyReview = { score: 2, reviewed_sha: null, source: 'body' }
    const rows: CiRow[] = [
      { id: 'main', kind: 'branch', repo: 'swcstudiospace/console', branch: 'main', head_sha: 'merge-sha', ci: 'pass', checks: [], greptile_review: mergedReview },
      { id: 'direct', kind: 'branch', repo: 'swcstudiospace/console', branch: 'direct', head_sha: 'direct-sha', ci: 'pass', checks: [], greptile_review: { score: null, reviewed_sha: null, source: 'not_applicable' } },
      { id: 'failed', kind: 'branch', repo: 'swcstudiospace/console', branch: 'failed', head_sha: 'failed-sha', ci: 'pass', checks: [], greptile_review: { score: null, reviewed_sha: null, source: 'unavailable' } },
      { id: 'body', kind: 'pull_request', repo: 'swcstudiospace/console', number: 13, title: 'Body-only evidence', branch: 'feature', head_sha: 'body-sha', ci: 'pass', checks: [], greptile_review: bodyReview },
    ]
    const snapshot = structuredClone(fixture) as Snapshot
    snapshot.sources!.github!.command_center!.ci_cd = rows
    snapshot.idle_sessions = [{ id: 'idle/13', title: 'Idle check', source: 'herdr', state: 'idle', display_id: 'Idle-13', wiring: { phase: '106', stage: 'idle', repo: 'swcstudiospace/console', branch: 'main', head_sha: 'merge-sha' } }]
    render(<CiCd snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)
    expect(screen.getByText('Greptile 0/5 · from #12')).toBeInTheDocument()
    expect(screen.getByText('No PR review for this commit')).toBeInTheDocument()
    expect(screen.getByText('Greptile review unavailable')).toBeInTheDocument()
    expect(screen.getByText('Greptile 2/5 · reviewed SHA not reported')).toBeInTheDocument()
    expect(screen.getByText(/Idle-13 · 106 · idle/)).toBeInTheDocument()
    expect(screen.queryByText(/Score unknown · Review source unknown/)).not.toBeInTheDocument()
  })
})
