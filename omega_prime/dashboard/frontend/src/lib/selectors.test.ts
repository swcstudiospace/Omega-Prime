import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import { correlateWiring, observedActivity, selectedTask, sortRepositories, taskSelection } from './selectors'
describe('dashboard selectors', () => {
  it('deduplicates active and finished identities and preserves server numbering', () => { const rows = taskSelection(fixture); expect(rows.filter(row => row.key === 'task:worker/1')).toHaveLength(1); expect(rows[0]?.item.display_id).toBe('00007 - Compile release'); expect(selectedTask(fixture, 'task:worker/1')?.id).toBe('worker/1') })
  it('includes only observed valid timestamped events', () => { expect(observedActivity(fixture).map(row => row.label)).toEqual(['running']) })
  it('sorts duplicate repository names deterministically and filters null fields safely', () => { const rows = fixture.sources?.github?.extra?.command_center?.repositories ?? []; expect(sortRepositories(rows, 'console', 'name')[0]?.id).toBe(42); expect(sortRepositories(rows, 'missing', 'name')).toEqual([]) })
  it('joins only on published branch, repo and head_sha evidence, rejecting ambiguous matches', () => { const row = fixture.sources!.github!.extra!.command_center!.ci_cd![0]!; const task = { ...fixture.active![0]!, wiring: { phase: '105-01', stage: 'complete', repo: row.repo!, branch: row.branch!, head_sha: row.head_sha!.slice(0, 10) } }; expect(correlateWiring(row, [task])).toEqual([task]); expect(correlateWiring(row, [task, { ...task, id: 'other' }])).toEqual([]); expect(correlateWiring(row, [{ ...task, wiring: { ...task.wiring, head_sha: null } }])).toEqual([]) })
})
