import type { Snapshot } from '../types'

export const fixture: Snapshot = {
  generated_at: '2026-01-01T00:00:00Z',
  active: [{
    id: 'worker/1', title: 'Compile release', source: 'herdr', state: 'running',
    owner: { label: 'Ove' }, display_id: '00007 - Compile release', seq: 7,
    timeline: [{ at: '2026-01-01T00:00:00Z', state: 'running' }],
  }],
  pr_gate: [],
  waiting_on_ove: [{ id: 'wait-1', ask: 'Approve deployment', blocks: 'release', display_id: '00008 - Approve deployment' }],
  finished: [{ id: 'worker/1', title: 'Compile release', result: 'success', display_id: '00007 - Compile release' }],
  sources: {
    local: { status: 'ok' },
    github: {
      status: 'ok',
      extra: {
        command_center: {
          repositories: [{
            id: 42, full_name: 'swcstudiospace/console', name: 'console',
            url: 'https://github.com/swcstudiospace/console', visibility: 'private',
            default_branch: 'main', pushed_at: '2026-01-01T00:00:00Z', language: 'TypeScript',
            open_pr_count: 2, latest_main_ci: { ci: 'success' },
          }],
          ci_cd: [{
            id: 'pr-12', kind: 'pull_request', repo: 'swcstudiospace/console', number: 12,
            title: 'Fix safe view', url: 'https://github.com/swcstudiospace/console/pull/12',
            branch: 'fix/safe-view', head_sha: 'abcdef123456', ci: 'success',
            checks: [{ id: 'check-1', name: 'build', status: 'completed', conclusion: 'success', duration_s: 22 }],
            greptile_review: { score: 4, reviewed_sha: 'abcdef123456', source: 'review', matches_head: true },
          }],
          recent_merges: [],
          meta: { partial: false },
        },
      },
    },
  },
}
