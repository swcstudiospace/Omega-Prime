import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import type { Snapshot } from '../types'
import { appendEvents, deriveEvents, filterEvents, gateReady, greptileScore, makeEvent, matchesTask, mergeEligible, payloadSummary, shortId, shouldToast, stabilizeSnapshot, type DashEvent } from './events'

const at = '2026-10-23T00:00:00Z'
const stamp = (snapshot: Snapshot | null, next: Snapshot) => deriveEvents(snapshot, next).map(draft => makeEvent(draft, { at, source: 'ws' }))
const types = (events: Array<{ type: string }>) => events.map(event => event.type)
const task = fixture.active![0]!

describe('deriveEvents', () => {
  it('emits one baseline event for the first snapshot instead of replaying state', () => {
    const [baseline, ...rest] = deriveEvents(null, fixture)
    expect(rest).toHaveLength(0)
    expect(baseline).toMatchObject({ type: 'snapshot.baseline', payload: { active: 1, waiting: 1, prs: 1 } })
    expect(deriveEvents(fixture, fixture)).toEqual([])
  })
  it('reports state changes, blocks, staleness and recovery per task', () => {
    const blocked = { ...fixture, active: [{ ...task, state: 'blocked' }] }
    const [event] = deriveEvents(fixture, blocked)
    expect(event).toMatchObject({ type: 'task.blocked', severity: 'error', taskId: 'worker/1', payload: { from: 'running', to: 'blocked' } })
    expect(event!.summary).toContain('00007 · Compile release · running → blocked')
    expect(types(deriveEvents(fixture, { ...fixture, active: [{ ...task, state: 'quiet' }] }))).toEqual(['task.state'])
    const stale = { ...fixture, active: [{ ...task, stale: { reason: 'pid_dead', since: at } }] }
    expect(deriveEvents(fixture, stale)[0]).toMatchObject({ type: 'task.stale', payload: { reason: 'pid_dead' } })
    expect(types(deriveEvents(stale, fixture))).toEqual(['task.recovered'])
  })
  it('reports new, finished and removed tasks and waiting requests', () => {
    const extra = { ...task, id: 'worker/9', title: '00044 - New work', display_id: '00099 - 00044 - New work' }
    expect(deriveEvents(fixture, { ...fixture, active: [task, extra] })[0]).toMatchObject({ type: 'task.added', taskId: 'worker/9' })
    const finished = { ...fixture, active: [], finished: [...fixture.finished!, { id: 'worker/1', title: 'Compile release', result: 'ok', state: 'done' }] }
    expect(types(deriveEvents(fixture, finished))).toEqual(['task.finished'])
    expect(types(deriveEvents(fixture, { ...fixture, active: [] }))).toEqual(['task.removed'])
    expect(types(deriveEvents(fixture, { ...fixture, waiting_on_ove: [] }))).toEqual(['waiting.cleared'])
    expect(deriveEvents({ ...fixture, waiting_on_ove: [] }, fixture)[0]).toMatchObject({ type: 'waiting.added', severity: 'warning', taskId: 'wait-1' })
  })
  it('detects the PR gate reaching 5/5, CI failures and merges, linked to their task', () => {
    const pr = fixture.pr_gate![0]!
    const five = { ...fixture, pr_gate: [{ ...pr, greptile: '5/5' }] }
    const events = deriveEvents(fixture, five)
    expect(types(events)).toEqual(['pr.greptile', 'pr.gate_ready'])
    expect(events[1]).toMatchObject({ taskId: 'worker/1', severity: 'success' })
    expect(deriveEvents(five, { ...five, pr_gate: [{ ...pr, greptile: '5/5', ci: 'fail' }] })[0]).toMatchObject({ type: 'pr.ci', severity: 'error', title: 'CI failed' })
    const github = fixture.sources!.github!
    const merged: Snapshot = { ...fixture, pr_gate: [], sources: { ...fixture.sources, github: { ...github, command_center: { ...github.command_center, recent_merges: [{ id: 'm-12', repo: pr.repo, number: 12, title: pr.title, merge_commit_sha: 'abcdef1234' }] } } } }
    const mergeEvents = deriveEvents(fixture, merged)
    // A merged PR leaves the gate as a merge, not as a silent close.
    expect(types(mergeEvents)).toEqual(['pr.merged'])
    expect(mergeEvents[0]).toMatchObject({ taskId: 'worker/1', payload: { sha: 'abcdef1' } })
  })
  it('reports source degradation and recovery', () => {
    const degraded = { ...fixture, sources: { ...fixture.sources, local: { status: 'error', error: 'boom' } } }
    expect(deriveEvents(fixture, degraded)[0]).toMatchObject({ type: 'source.degraded', severity: 'error' })
    expect(types(deriveEvents(degraded, fixture))).toEqual(['source.recovered'])
  })
})

const github = fixture.sources!.github!
const withReview = (review: Record<string, unknown> | null): Snapshot => ({ ...fixture, sources: { ...fixture.sources, github: { ...github, command_center: { ...github.command_center, ci_cd: github.command_center!.ci_cd!.map(row => ({ ...row, greptile_review: review })) } } } })

describe('review-gated readiness and edge transitions', () => {
  it('never declares the gate ready on a 5/5 that is not confirmed for the current head', () => {
    const pr = { ...fixture.pr_gate![0]!, greptile: '5/5', ci: 'pass' }
    expect(gateReady(pr, withReview({ score: 5, reviewed_sha: null, source: 'issue_comment', matches_head: null }))).toBe(false)
    expect(gateReady(pr, withReview({ score: 5, reviewed_sha: 'old', source: 'review', matches_head: false }))).toBe(false)
    expect(gateReady(pr, withReview(null))).toBe(false)
    expect(gateReady(pr, { ...fixture, sources: {} })).toBe(false)
    // A score from a PR body after a new push: no gate event, no success toast.
    const unverified = withReview({ score: 5, reviewed_sha: null, source: 'pr_body', matches_head: null })
    expect(types(deriveEvents(unverified, { ...unverified, pr_gate: [{ ...pr, ci: 'success' }] }))).toEqual(['pr.greptile'])
  })
  it('emits the actionable event for rows first seen blocked or with CI red', () => {
    const blocked = { ...task, id: 'worker/5', state: 'blocked', display_id: '00011 - Late arrival' }
    const events = deriveEvents(fixture, { ...fixture, active: [task, blocked] })
    expect(types(events)).toEqual(['task.added', 'task.blocked'])
    expect(events[1]).toMatchObject({ taskId: 'worker/5', severity: 'error', payload: { from: null, to: 'blocked' } })
    const red = { ...fixture.pr_gate![0]!, id: 'gate-13', number: 13, ci: 'failure' }
    const prEventsSeen = deriveEvents(fixture, { ...fixture, pr_gate: [...fixture.pr_gate!, red] })
    expect(types(prEventsSeen)).toEqual(['pr.opened', 'pr.ci'])
    expect(shouldToast(makeEvent(prEventsSeen[1]!, { at, source: 'ws' }))).toBe(true)
  })
  it('treats a pane parking in idle_sessions as a state change, not a removal', () => {
    const parked = { ...fixture, active: [], idle_sessions: [...fixture.idle_sessions!, { ...task, state: 'quiet' }] }
    expect(types(deriveEvents(fixture, parked))).toEqual(['task.state'])
    expect(types(deriveEvents(parked, fixture))).toEqual(['task.state'])
  })
  it('reports a later completion of an id that already finished', () => {
    const first = fixture.finished![0]!
    const rerun = { ...fixture, finished: [first, { ...first, result: 'failed', state: 'failed', finished_at: '2026-01-02T00:00:00Z', receipt: 'receipt-4' }] }
    const [event] = deriveEvents(fixture, rerun)
    expect(event).toMatchObject({ type: 'task.finished', severity: 'error', taskId: 'finished/3', payload: { result: 'failed' } })
    expect(deriveEvents(rerun, rerun)).toEqual([])
    expect(deriveEvents(rerun, { ...rerun, finished: [...rerun.finished].reverse() })).toEqual([])
  })
  it('alerts on any collector failure status, not only "error"', () => {
    const unreadable = { ...fixture, sources: { ...fixture.sources, local: { status: 'unreadable', error: 'bad json' } } }
    const [event] = deriveEvents(fixture, unreadable)
    expect(event).toMatchObject({ type: 'source.degraded', severity: 'error' })
    expect(shouldToast(makeEvent(event!, { at, source: 'ws' }))).toBe(true)
    const partial = { ...fixture, sources: { ...fixture.sources, github: { ...github, status: 'degraded' } } }
    expect(deriveEvents(fixture, partial)[0]).toMatchObject({ type: 'source.degraded', severity: 'warning' })
  })
  it('does not replay merges after a failed first GitHub load', () => {
    const failed: Snapshot = { ...fixture, sources: { ...fixture.sources, github: { status: 'error', error: 'rate limited', command_center: { ...github.command_center, recent_merges: [] } } } }
    const recovered: Snapshot = { ...fixture, sources: { ...fixture.sources, github: { ...github, command_center: { ...github.command_center, recent_merges: [{ id: 'm-old', repo: 'swcstudiospace/console', number: 3, title: 'Old work' }] } } } }
    expect(types(deriveEvents(stabilizeSnapshot(null, failed), recovered))).not.toContain('pr.merged')
    // From a good baseline, a genuinely new merge is still news.
    const newer: Snapshot = { ...recovered, sources: { ...recovered.sources, github: { ...github, command_center: { ...github.command_center, recent_merges: [{ id: 'm-new', repo: 'swcstudiospace/console', number: 4, title: 'New work' }, { id: 'm-old', repo: 'swcstudiospace/console', number: 3, title: 'Old work' }] } } } }
    expect(types(deriveEvents(recovered, newer))).toEqual(['pr.merged'])
  })
  it('keeps the merge linked to its task when the PR left the gate before the merge list refreshed', () => {
    const left: Snapshot = { ...fixture, pr_gate: [] }
    const mergedLate: Snapshot = { ...left, sources: { ...left.sources, github: { ...github, command_center: { ...github.command_center, recent_merges: [{ id: 'm-12', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view', url: 'https://github.com/swcstudiospace/console/pull/12' }] } } } }
    const [event] = deriveEvents(left, mergedLate)
    expect(event).toMatchObject({ type: 'pr.merged', taskId: 'worker/1' })
  })
  it('carries forward only the failed source\'s rows and keeps other collectors live', () => {
    const fileTask = { ...task, id: 'tasks:file-1', title: 'From the task file', source: 'tasks_json' }
    const fileWait = { id: 'tasks:wait-2', ask: 'Pick a name', source: 'tasks_json' }
    const good: Snapshot = { ...fixture, active: [task, fileTask], waiting_on_ove: [...fixture.waiting_on_ove!, fileWait], sources: { ...fixture.sources, tasks_json: { status: 'ok' } } }
    const blockedHerdr = { ...task, state: 'blocked' }
    const down: Snapshot = { ...good, active: [blockedHerdr], finished: [], waiting_on_ove: fixture.waiting_on_ove, sources: { ...good.sources, tasks_json: { status: 'unreadable', error: 'x' } } }
    const stable = stabilizeSnapshot(good, down)
    expect(stable.active?.map(row => [row.id, row.state])).toEqual([['worker/1', 'blocked'], ['tasks:file-1', 'running']])
    expect(stable.finished?.map(row => row.id)).toEqual(['finished/3'])
    expect(stable.waiting_on_ove?.map(row => row.id)).toEqual(['wait-1', 'tasks:wait-2'])
    expect(stable.sources?.tasks_json?.status).toBe('unreadable')
    // The Herdr pane that blocked during the task-file outage still alerts.
    expect(types(deriveEvents(good, stable))).toEqual(['task.blocked', 'source.degraded'])
    // Recovery replays nothing that was carried.
    const back = stabilizeSnapshot(stable, { ...good, active: [blockedHerdr, fileTask] })
    expect(types(deriveEvents(stable, back))).toEqual(['source.recovered'])
    expect(stabilizeSnapshot(fixture, { ...fixture, active: [] }).active).toEqual([])
  })
  it('treats the first task-file read after a failed start as a baseline, not news', () => {
    const failedStart = stabilizeSnapshot(null, { ...fixture, finished: [], sources: { ...fixture.sources, tasks_json: { status: 'error', error: 'missing' } } })
    const fileTask = { ...task, id: 'tasks:file-1', source: 'tasks_json' }
    const read: Snapshot = { ...fixture, active: [task, fileTask], waiting_on_ove: [...fixture.waiting_on_ove!, { id: 'tasks:wait-2', ask: 'Old ask', source: 'tasks_json' }], sources: { ...fixture.sources, tasks_json: { status: 'ok' } } }
    const recovered = stabilizeSnapshot(failedStart, read)
    expect(types(deriveEvents(failedStart, recovered))).toEqual(['source.recovered'])
    // From that baseline, a genuinely new task-file row is news again.
    const newer = stabilizeSnapshot(recovered, { ...read, active: [...read.active!, { ...fileTask, id: 'tasks:file-2' }] })
    expect(types(deriveEvents(recovered, newer))).toEqual(['task.added'])
  })
  it('announces merges made during a GitHub outage once it recovers', () => {
    const merge = (id: string, number: number) => ({ id, repo: 'swcstudiospace/console', number, title: `PR ${number}` })
    const withMerges = (status: string, merges: ReturnType<typeof merge>[]): Snapshot => ({ ...fixture, sources: { ...fixture.sources, github: { status, error: status === 'ok' ? undefined : 'timeout', command_center: { ...fixture.sources!.github!.command_center, recent_merges: merges } } } })
    const good = stabilizeSnapshot(null, withMerges('ok', [merge('m-old', 3)]))
    const outage = stabilizeSnapshot(good, withMerges('error', [merge('m-old', 3)]))
    expect(types(deriveEvents(good, outage))).not.toContain('pr.merged')
    const recovered = stabilizeSnapshot(outage, withMerges('ok', [merge('m-new', 4), merge('m-old', 3)]))
    expect(deriveEvents(outage, recovered).filter(event => event.type === 'pr.merged').map(event => event.payload.number)).toEqual([4])
    expect(deriveEvents(recovered, stabilizeSnapshot(recovered, withMerges('ok', [merge('m-new', 4), merge('m-old', 3)])))).toEqual([])
  })
  it('links a merge to no task when several tasks claim it by link or branch', () => {
    const url = 'https://github.com/swcstudiospace/console/pull/12'
    const linkedOnly = { ...task, id: 'worker/7', wiring: null, links: [{ label: 'PR', path_or_url: url }] }
    const both = { ...task, links: [{ label: 'PR', path_or_url: url }] }
    const left: Snapshot = { ...fixture, active: [both, linkedOnly], pr_gate: [] }
    const merged: Snapshot = { ...left, sources: { ...left.sources, github: { ...fixture.sources!.github!, command_center: { ...fixture.sources!.github!.command_center, recent_merges: [{ id: 'm-12', repo: 'swcstudiospace/console', number: 12, title: 'Fix safe view', url }] } } } }
    expect(deriveEvents(left, merged)[0]).toMatchObject({ type: 'pr.merged', taskId: null })
    const branchOnly = { ...task, id: 'worker/8', links: [] }
    const split: Snapshot = { ...left, active: [linkedOnly, branchOnly] }
    expect(deriveEvents(split, { ...merged, active: split.active })[0]).toMatchObject({ type: 'pr.merged', taskId: null })
  })
  it('matches waiting requests to the task they are linked to', () => {
    const waiting = makeEvent({ type: 'waiting.added', severity: 'warning', taskId: 'wait-9', title: '', summary: '', payload: { related_task: 'worker/1' } }, { at, source: 'ws' })
    const cleared = makeEvent({ type: 'waiting.cleared', severity: 'success', taskId: 'wait-9', title: '', summary: '', payload: { related_task: 'worker/1' } }, { at, source: 'ws' })
    expect(matchesTask(waiting, 'worker/1')).toBe(true); expect(matchesTask(cleared, 'worker/1')).toBe(true)
    expect(matchesTask(waiting, 'worker/2')).toBe(false); expect(matchesTask(waiting, 'wait-9')).toBe(true)
  })
})

describe('event helpers', () => {
  it('scores Greptile and separates the gate from merge eligibility for drafts', () => {
    expect(greptileScore('5/5')).toBe(5); expect(greptileScore('not reviewed')).toBeNull(); expect(greptileScore(4)).toBe(4)
    const pr = { ...fixture.pr_gate![0]!, greptile: '5/5', ci: 'pass' }
    expect(gateReady(pr, fixture)).toBe(true); expect(mergeEligible(pr, fixture)).toBe(true)
    expect(gateReady({ ...pr, draft: true }, fixture)).toBe(true); expect(mergeEligible({ ...pr, draft: true }, fixture)).toBe(false)
    expect(gateReady({ ...pr, mergeable: false }, fixture)).toBe(false); expect(gateReady({ ...pr, ci: 'pending' }, fixture)).toBe(false)
  })
  it('keeps the log newest first and bounded', () => {
    const batch = Array.from({ length: 6 }, (_, i) => makeEvent({ type: 'task.state', severity: 'info', taskId: null, title: `e${i}`, summary: '', payload: {} }, { at, source: 'ws' }))
    const log = appendEvents(appendEvents([], batch.slice(0, 3), 4), batch.slice(3), 4)
    expect(log.map(event => event.title)).toEqual(['e5', 'e4', 'e3', 'e2'])
  })
  it('filters by category, alerts and text, and summarises payloads', () => {
    const events = stamp(fixture, { ...fixture, active: [{ ...task, state: 'blocked' }], pr_gate: [{ ...fixture.pr_gate![0]!, greptile: '5/5' }] })
    expect(filterEvents(events, 'task')).toHaveLength(1)
    expect(filterEvents(events, 'pr')).toHaveLength(2)
    expect(filterEvents(events, 'alerts').map(event => event.type)).toEqual(['task.blocked'])
    expect(filterEvents(events, 'all', 'gate')).toHaveLength(1)
    expect(payloadSummary({ a: 1, b: null, c: 'x' })).toBe('a=1 · c=x')
  })
  it('toasts only the events Ove acts on', () => {
    const make = (type: DashEvent['type'], severity: DashEvent['severity'] = 'info') => makeEvent({ type, severity, taskId: null, title: '', summary: '', payload: {} }, { at, source: 'ws' })
    expect(shouldToast(make('task.blocked', 'error'))).toBe(true)
    expect(shouldToast(make('pr.gate_ready', 'success'))).toBe(true)
    expect(shouldToast(make('pr.merged', 'success'))).toBe(true)
    expect(shouldToast(make('pr.ci', 'error'))).toBe(true)
    expect(shouldToast(make('pr.ci', 'success'))).toBe(false)
    expect(shouldToast(make('snapshot.baseline'))).toBe(false)
    expect(shouldToast(make('terminal.frame'))).toBe(false)
  })
  it('reads the short task number from the canonical display id', () => {
    expect(shortId({ display_id: '00374 - 00039 - Sandbox' })).toBe('00374')
    expect(shortId({ display_id: null, seq: 7 })).toBe('00007')
    expect(shortId({})).toBe('—')
  })
})
