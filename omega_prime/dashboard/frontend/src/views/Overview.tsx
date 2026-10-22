import { useState } from 'react'
import type { Snapshot, Task, WaitingItem } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { SummaryCards } from '../components/SummaryCards'
import { SourceHealth } from '../components/SourceHealth'
import { ViewState } from '../components/ViewState'
import { BlurFade } from '../components/BlurFade'
import { AnimatedShinyText } from '../components/AnimatedShinyText'
import { githubFreshness, idleTasks, observedActivity, waitingRows, workingTasks } from '../lib/selectors'
import { formatSydney } from '../lib/time'
import { taskHref } from '../lib/routes'
export function Overview({ snapshot, loading, error, receivedAt, refresh }: DashboardState & { snapshot: Snapshot }) {
  const [idleOpen, setIdleOpen] = useState(false)
  const active = workingTasks(snapshot), waiting = waitingRows(snapshot).map(row => row.item), prs = snapshot.pr_gate ?? [], finished = snapshot.finished ?? [], idle = idleTasks(snapshot)
  const cards = [{ label: 'Active work', value: active.length, note: 'Non-terminal runner tasks', icon: '◈', bucket: 'working' as const }, { label: 'Waiting on Ove', value: waiting.length, note: 'Recorded requests', icon: '⌁', bucket: 'waiting' as const }, { label: 'PR gate', value: prs.length, note: 'Open merge checks', icon: '⇄', bucket: 'pr-gate' as const }, { label: 'Finished', value: new Set(finished.map(item => item.id)).size, note: 'Unique task identities', icon: '✓', bucket: 'finished' as const }]
  const activity = observedActivity(snapshot)
  const merges = snapshot.sources?.github?.command_center?.recent_merges ?? []
  const githubMeta = snapshot.sources?.github?.command_center?.meta
  const freshness = githubFreshness(snapshot)
  const partial = freshness.partial ? ['GitHub source data is degraded; local task and health data remains available'] : []
  const tasks: Array<Task | WaitingItem> = [...active.slice(0, 5), ...waiting.slice(0, 5)]
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} sourceUpdatedAt={freshness.updatedAt} stale={freshness.stale} retry={refresh} githubMeta={githubMeta} partial={partial}><div className="view-stack"><BlurFade><section className="welcome-panel"><p className="eyebrow">WORKFLOW SIGNAL · READ ONLY</p><h2><AnimatedShinyText>Clarity across the work in flight.</AnimatedShinyText></h2><p>Live task evidence, source health and verified repository activity.</p></section></BlurFade><SummaryCards items={cards} /><section className="panel overview-task-group"><div className="section-heading"><div><p className="eyebrow">CURRENT WORK</p><h2>Task details</h2></div><a href="#/tasks">View tasks</a></div>{tasks.length ? <ul className="overview-task-list">{tasks.map((item, index) => <li key={`${item.id}-${index}`}><span>{item.display_id ?? 'ID unavailable'}</span><a href={taskHref(item.id)}>{'ask' in item ? item.ask : item.title}</a><span className="state-pill">{'ask' in item ? 'Waiting on Ove' : item.state}</span></li>)}</ul> : <p>No task records available.</p>}</section><section className="panel idle-group"><button className="idle-disclosure" aria-expanded={idleOpen} onClick={() => setIdleOpen(value => !value)}>Idle sessions ({idle.length})</button>{idleOpen && (idle.length ? <ul className="overview-task-list">{idle.map(item => <li key={item.id}><span>{item.display_id ?? 'ID unavailable'}</span><a href={taskHref(item.id)}>{item.title}</a><span className="state-pill">Idle</span></li>)}</ul> : <p>No idle sessions.</p>)}</section><div className="overview-columns"><section className="panel"><div className="section-heading"><div><p className="eyebrow">OBSERVED EVENTS</p><h2>Activity feed</h2></div><span className="section-count">Persisted observations</span></div>{activity.length ? <ol className="activity-list">{activity.slice(0, 12).map((event, i) => <li key={`${event.id}-${event.at}-${i}`}><time>{formatSydney(event.at)}</time><span>{event.label}</span><code>{event.id}</code></li>)}</ol> : <p className="empty-state">No persisted timeline or check events are available.</p>}</section><section className="panel"><div className="section-heading"><div><p className="eyebrow">GITHUB EVIDENCE</p><h2>Recent merges</h2></div></div>{merges.length ? <ul className="merge-list">{merges.slice(0, 8).map(merge => <li key={merge.id}><div><strong>{merge.repo} #{merge.number}</strong><p>{merge.title}</p></div><time>{formatSydney(merge.merged_at)}</time></li>)}</ul> : <p className="empty-state">No verified recent merge records available.</p>}</section></div><SourceHealth sources={snapshot.sources} /></div></ViewState>
}
