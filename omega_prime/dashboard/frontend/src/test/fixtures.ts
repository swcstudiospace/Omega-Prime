import type { Snapshot } from '../types'

export const fixture: Snapshot = {
  generated_at: '2026-01-01T00:00:00Z',
  active: [{
    id: 'worker/1', title: 'Compile release', source: 'herdr', state: 'running',
    owner: { label: 'Ove' }, model: 'codex', plan: 'release plan', display_id: '00007 - Compile release', seq: 7,
    started_at: '2026-01-01T00:00:00Z', last_activity_at: '2026-01-01T00:05:00Z', detail: 'Compile and verify the release.',
    links: [{ label: 'Linear OPS-1', path_or_url: 'https://linear.app/example/issue/OPS-1' }, { label: 'Unsafe link', path_or_url: 'javascript:alert(1)' }],
    wiring: { phase: '106-04', stage: 'implementation', repo: 'swcstudiospace/console', branch: 'fix/safe-view', head_sha: 'abcdef123456', worktree: '/worktrees/release' },
    timeline: [{ at: '2026-01-01T00:00:00Z', state: 'running' }],
  }],
  idle_sessions: [{ id: 'idle/2', title: 'Quiet sandbox', source: 'sandbox_e2e', state: 'idle', owner: { label: 'Agent' }, display_id: '00009 - Quiet sandbox', last_activity_at: '2026-01-01T00:03:00Z' }],
  pr_gate: [{ id: 'gate-12', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view', branch: 'fix/safe-view', draft: false, mergeable: true, ci: 'success', greptile: '4/5', merge_rule: 'ready', updated_at: '2026-01-01T00:04:00Z' }],
  waiting_on_ove: [{ id: 'wait-1', ask: 'Approve deployment', blocks: 'release', display_id: '00008 - Approve deployment' }],
  finished: [{ id: 'finished/3', title: 'Publish report', result: 'ok', summary: 'Report published.', receipt: 'receipt-3', state: 'done', source: 'tasks_json', model: 'codex', display_id: '00010 - Publish report', wiring: { phase: null, stage: null, repo: 'swcstudiospace/console', branch: 'main', head_sha: '1234567890', worktree: '/worktrees/report' } }],
  sources: {
    local: { status: 'ok', items: [{ id: 'event-1', task_id: 'worker/1', at: '2026-01-01T00:06:00Z', event: 'Task resumed' }] },
    github: {
      status: 'ok',
      command_center: {
        repositories: [{ id: 42, full_name: 'swcstudiospace/console', name: 'console', url: 'https://github.com/swcstudiospace/console', visibility: 'private', default_branch: 'main', pushed_at: '2026-01-01T00:00:00Z', language: 'TypeScript', open_pr_count: 2, latest_main_ci: { ci: 'success' } }],
        ci_cd: [{ id: 'pr-12', kind: 'pull_request', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view', url: 'https://github.com/swcstudiospace/console/pull/12', branch: 'fix/safe-view', head_sha: 'abcdef123456', ci: 'success', checks: [{ id: 'check-1', name: 'build', status: 'completed', conclusion: 'success', duration_s: 22 }], greptile_review: { score: 4, reviewed_sha: 'abcdef123456', source: 'review', matches_head: true } }],
        recent_merges: [], meta: { partial: false },
      },
    },
  },
}
