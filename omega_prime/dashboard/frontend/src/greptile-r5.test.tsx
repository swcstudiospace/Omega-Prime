import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { CiRow, Snapshot, Task } from './types'
import { App } from './App'
import { CiCd } from './views/CiCd'
import { Tasks } from './views/Tasks'
import { bucketRows, taskPrRows, workingTasks } from './lib/selectors'
import { safeHref } from './lib/links'
import { fixture } from './test/fixtures'

const sources: string[] = []
class RecordingEventSource {
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor(url: string) { sources.push(url) }
  addEventListener() {}
  close() {}
}
const state = vi.hoisted(() => ({ data: null as Snapshot | null }))
vi.mock('./hooks/useDashboard', () => ({ useDashboard: () => ({ data: state.data, loading: false, error: null, receivedAt: Date.now(), live: true, refresh: vi.fn() }) }))
beforeEach(() => { sources.length = 0; vi.stubGlobal('EventSource', RecordingEventSource); vi.stubGlobal('WebSocket', undefined); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline'))); window.history.replaceState(null, ''); window.location.hash = '#/overview' })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const waitingTask: Task = { id: 'tasks:ask', title: 'Needs a decision', source: 'tasks_json', state: 'waiting_on_ove', display_id: '00090 - Needs a decision' }

describe('Greptile round 5 regressions', () => {
  it('puts waiting_on_ove tasks in Waiting, not Working, without duplicates', () => {
    const snapshot: Snapshot = { active: [waitingTask, { ...waitingTask, id: 'tasks:busy', state: 'working' }], waiting_on_ove: [{ id: 'tasks:other', ask: 'Approve?' }] }
    expect(workingTasks(snapshot).map(task => task.id)).toEqual(['tasks:busy'])
    expect(bucketRows(snapshot, 'waiting').map(row => row.key)).toEqual(['wait:tasks:other', 'task:tasks:ask'])
    const both: Snapshot = { ...snapshot, waiting_on_ove: [{ id: 'tasks:ask', ask: 'Pick one' }] }
    expect(bucketRows(both, 'waiting').map(row => row.key)).toEqual(['wait:tasks:ask'])
  })

  it('never borrows PR evidence by branch name without a repository', () => {
    const rows: CiRow[] = [{ id: 'pr', kind: 'pull_request', repo: 'swcstudiospace/console', number: 9, branch: 'feat/x', url: 'https://github.com/swcstudiospace/console/pull/9' }]
    const snapshot = { sources: { github: { status: 'ok', command_center: { ci_cd: rows } } } } as unknown as Snapshot
    const task = (repo: string | null): Task => ({ id: 't', title: 't', source: 'wiring', state: 'working', wiring: { phase: null, stage: null, head_sha: null, branch: 'feat/x', repo } })
    expect(taskPrRows(snapshot, task(null))).toEqual([])
    expect(taskPrRows(snapshot, task('swcstudiospace/Omega-Prime'))).toEqual([])
    expect(taskPrRows(snapshot, task('swcstudiospace/console')).map(row => row.number)).toEqual([9])
    const linked = { ...task(null), links: [{ label: 'PR', path_or_url: 'https://github.com/swcstudiospace/console/pull/9' }] }
    expect(taskPrRows(snapshot, linked).map(row => row.number)).toEqual([9])
  })

  it('keeps local filesystem paths as text and links only served routes', () => {
    for (const value of ['/workspace/runs/104/phase.log', '~/notes.md', './phase.log', 'C:\\runs\\log']) expect(safeHref(value, 'http://localhost')).toBeNull()
    expect(safeHref('/api/snapshot', 'http://localhost')).toBe('http://localhost/api/snapshot')
    expect(safeHref('/legacy', 'http://localhost')).toBe('http://localhost/legacy')
  })

  it('labels finished rows that carry no state', () => {
    const snapshot: Snapshot = { finished: [{ id: 'tasks:done', title: 'Shipped', result: 'failed', state: null, display_id: '00091 - Shipped' }] }
    render(<Tasks snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="finished" notice={null} onTask={vi.fn()} />)
    expect(screen.getByText('Finished · failed')).toBeInTheDocument()
  })

  it('marks a merged-PR score whose reviewed SHA is missing', () => {
    const snapshot = structuredClone(fixture) as Snapshot
    snapshot.sources!.github!.command_center!.ci_cd = [{ id: 'main', kind: 'branch', repo: 'swcstudiospace/console', branch: 'main', head_sha: 'm', ci: 'pass', checks: [], greptile_review: { score: 5, reviewed_sha: null, source: 'merged_pr', pr_number: 12 } as NonNullable<CiRow['greptile_review']> }]
    render(<CiCd snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)
    expect(screen.getByText('Greptile 5/5 · from #12 · reviewed SHA not reported')).toBeInTheDocument()
  })

  it('opens the waiting request that was clicked when a task shares its id', async () => {
    state.data = { active: [{ ...waitingTask, id: 'tasks:shared', state: 'working' }], waiting_on_ove: [{ id: 'tasks:shared', ask: 'Approve the deploy?', blocks: 'release', display_id: '00092 - ask' }] }
    window.location.hash = '#/tasks?bucket=waiting'
    render(<App />)
    const links = await screen.findAllByRole('link', { name: /Approve the deploy\?/ })
    const link = links.find(element => element.classList.contains('task-title-link'))!
    fireEvent.click(link)
    act(() => { window.location.hash = '#/tasks/tasks%3Ashared'; window.dispatchEvent(new HashChangeEvent('hashchange')) })
    await waitFor(() => expect(screen.getByRole('heading', { name: /Approve the deploy\?/, level: 2 })).toBeInTheDocument())
    expect(screen.getByText('release')).toBeInTheDocument()
  })

  it('streams the related pane terminal for a blocked-pane request', async () => {
    state.data = { idle_sessions: [{ id: 'herdr:w2:p1', title: 'Pane', source: 'herdr_pane', state: 'idle' }], waiting_on_ove: [{ id: 'wait:herdr:w2:p1', ask: 'blocked pane', related_task_id: 'herdr:w2:p1' }] }
    window.location.hash = '#/tasks/wait%3Aherdr%3Aw2%3Ap1'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /blocked pane/, level: 2 })).toBeInTheDocument())
    await waitFor(() => expect(sources).toContain('/api/tasks/herdr%3Aw2%3Ap1/terminal'))
  })
})
