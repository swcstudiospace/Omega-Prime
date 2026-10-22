import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import type { Snapshot } from '../types'
import { appendEvents, deriveEvents, filterEvents, gateReady, greptileScore, makeEvent, mergeEligible, payloadSummary, shortId, shouldToast, type DashEvent } from './events'

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

describe('event helpers', () => {
  it('scores Greptile and separates the gate from merge eligibility for drafts', () => {
    expect(greptileScore('5/5')).toBe(5); expect(greptileScore('not reviewed')).toBeNull(); expect(greptileScore(4)).toBe(4)
    const pr = { ...fixture.pr_gate![0]!, greptile: '5/5', ci: 'pass' }
    expect(gateReady(pr)).toBe(true); expect(mergeEligible(pr)).toBe(true)
    expect(gateReady({ ...pr, draft: true })).toBe(true); expect(mergeEligible({ ...pr, draft: true })).toBe(false)
    expect(gateReady({ ...pr, mergeable: false })).toBe(false); expect(gateReady({ ...pr, ci: 'pending' })).toBe(false)
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
