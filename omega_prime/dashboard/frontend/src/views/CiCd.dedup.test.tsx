import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { CiCd } from './CiCd'
import { fixture } from '../test/fixtures'
import type { CiRow, Snapshot } from '../types'

afterEach(() => cleanup())

function dashboardProps(snapshot: Snapshot) { return { snapshot, data: snapshot, loading: false, error: null, receivedAt: Date.now(), live: true, refresh: vi.fn() } }

function snapshotWith(rows: CiRow[], latestMain: Array<{ repo: string; branch: string; head_sha: string; ci: string; checks: Array<{ name: string; status: string }> }>): Snapshot {
  return {
    ...fixture,
    sources: {
      ...fixture.sources,
      github: {
        ...fixture.sources?.github,
        command_center: {
          ...fixture.sources?.github?.command_center,
          repositories: latestMain.map((row, index) => ({
            id: index + 1, full_name: row.repo, name: row.repo.split('/').at(-1) ?? row.repo,
            default_branch: row.branch, latest_main_ci: { head_sha: row.head_sha, ci: row.ci, checks: row.checks },
          })),
          ci_cd: rows,
        },
      },
    },
  }
}

function branch(id: string, repo: string, branchName: string, sha: string, checkName: string): CiRow {
  return { id, kind: 'branch', repo, branch: branchName, head_sha: sha, ci: `backend ${checkName}`, checks: [{ id: `${id}-check`, name: checkName, status: 'completed' }] }
}

describe('CI/CD branch identity', () => {
  it('prefers backend branch rows even when fallback SHA is stale', () => {
    const backend = branch('omega-backend', 'swcstudiospace/console', 'main', 'backend-sha', 'backend-build')
    const snapshot = snapshotWith(
      [backend, branch('fleet-backend', 'coding-fleet/runner', 'main', 'fleet-sha', 'fleet-build'), {
        id: 'pr-12', kind: 'pull_request', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view',
        branch: 'fix/safe-view', head_sha: 'pr-sha', ci: 'success', checks: [{ name: 'pr-check', status: 'completed' }],
      }],
      [
        { repo: 'swcstudiospace/console', branch: 'main', head_sha: 'stale-fallback-sha', ci: 'fallback failure', checks: [{ name: 'fallback-only-check', status: 'completed' }] },
        { repo: 'coding-fleet/runner', branch: 'main', head_sha: 'fleet-fallback-sha', ci: 'fallback success', checks: [{ name: 'fleet-fallback-check', status: 'completed' }] },
      ],
    )

    render(<CiCd {...dashboardProps(snapshot)} />)

    expect(screen.getAllByRole('heading', { name: 'main' })).toHaveLength(2)
    expect(screen.getByText('backend backend-build')).toBeInTheDocument()
    expect(screen.getByText('backend-build')).toBeInTheDocument()
    expect(screen.queryByText('fallback-only-check')).not.toBeInTheDocument()
    expect(screen.getByText('fleet-build')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Fix safe view/ })).toBeInTheDocument()
  })

  it('adds fallback only when repository branch has no backend row', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const snapshot = snapshotWith(
      [
        branch('duplicate-first', 'swcstudiospace/console', 'main', 'first-sha', 'first-build'),
        branch('duplicate-second', 'swcstudiospace/console', 'main', 'second-sha', 'second-build'),
        branch('feature', 'swcstudiospace/console', 'feature/next', 'feature-sha', 'feature-build'),
        { id: 'pr-12', kind: 'pull_request', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view', branch: 'fix/safe-view', ci: 'success', checks: [] },
        { id: 'other-pr-12', kind: 'pull_request', repo: 'coding-fleet/runner', number: 12, title: 'Runner update', branch: 'runner/update', ci: 'pending', checks: [] },
      ],
      [
        { repo: 'swcstudiospace/console', branch: 'main', head_sha: 'fallback-main', ci: 'fallback main', checks: [{ name: 'fallback-main-check', status: 'completed' }] },
        { repo: 'swcstudiospace/console', branch: 'release', head_sha: 'fallback-release', ci: 'fallback release', checks: [{ name: 'fallback-release-check', status: 'completed' }] },
      ],
    )

    const view = render(<CiCd {...dashboardProps(snapshot)} />)
    expect(screen.getAllByRole('heading', { name: 'main' })).toHaveLength(1)
    expect(screen.getByText('first-build')).toBeInTheDocument()
    expect(screen.queryByText('second-build')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'release' })).toBeInTheDocument()
    expect(screen.getByText('fallback-release-check')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'feature/next' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Fix safe view/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Runner update/ })).toBeInTheDocument()

    view.rerender(<CiCd {...dashboardProps({ ...snapshot })} />)
    expect(error).not.toHaveBeenCalledWith(expect.stringContaining('same key'))
    error.mockRestore()
  })
})
