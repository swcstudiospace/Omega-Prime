import { useMemo } from 'react'
import type { FinishedItem, Snapshot, Task, WaitingItem } from '../types'
import { formatSydney } from '../lib/time'
import { safeHref } from '../lib/links'
import { taskPrRows, isTask, isWaiting } from '../lib/selectors'
import { reviewLabel } from '../lib/review'
import { bucketHref, type TaskBucket } from '../lib/routes'
import { TerminalPanel } from './TerminalPanel'
import { EventStream } from './EventStream'
import { useEvents } from '../lib/eventContext'
import { matchesTask } from '../lib/events'
type Item = Task | WaitingItem | FinishedItem
type Props = { item: Item; taskId: string; snapshot: Snapshot; backBucket: TaskBucket; loading: boolean }
const value = (field: string | null | undefined) => field || 'Unavailable'
export function TaskDetail({ item, taskId, snapshot, backBucket, loading }: Props) {
  const { events, log } = useEvents()
  // Task-scoped stream: dashboard events for this identity (and for a
  // waiting request, its related task) plus this task's terminal lifecycle.
  const relatedId = isWaiting(item) ? item.related_task_id ?? null : null
  const taskEvents = useMemo(() => events.filter(event => matchesTask(event, item.id) || matchesTask(event, taskId) || (relatedId !== null && matchesTask(event, relatedId))), [events, item.id, taskId, relatedId])
  const task = isTask(item)
  const waiting = isWaiting(item)
  const title = waiting ? item.ask : item.title
  const numberedTitle = item.display_id ? item.display_id.toLocaleLowerCase().includes(title.toLocaleLowerCase()) ? item.display_id : `${item.display_id} - ${title}` : title
  const finished = snapshot.finished?.find(record => record.id === item.id)
  const timeline = item.timeline ?? []
  const wiring = ('wiring' in item ? item.wiring : null) ?? finished?.wiring ?? null
  const ownerRecord = ('owner' in item ? item.owner : null) ?? finished?.owner
  const owner = ownerRecord?.label ?? ownerRecord?.ref ?? ownerRecord?.kind ?? null
  const model = ('model' in item ? item.model : null) ?? finished?.model
  const state = task ? item.state : waiting ? 'Waiting on Ove' : ('state' in item ? item.state : null) ?? 'Finished'
  const recordedLinks = 'links' in item ? item.links ?? [] : []
  const links = recordedLinks.length ? recordedLinks : finished?.links ?? []
  const prs = taskPrRows(snapshot, item)
  const fields: Array<[string, string]> = [
    ['State', value(state)], ['Result', value('result' in item ? item.result : finished?.result ?? null)], ['Owner / agent', value(owner)],
    ['Model', value(model)], ['Plan', value('plan' in item ? item.plan ?? finished?.plan : finished?.plan)], ['Phase', value(wiring?.phase ?? ('phase' in item ? item.phase : null))],
    ['Stage', value(wiring?.stage ?? ('stage' in item ? item.stage : null))], ['Source', value('source' in item ? item.source : null)],
    ['Started', formatSydney('started_at' in item ? item.started_at ?? finished?.started_at : finished?.started_at)], ['Last activity', formatSydney('last_activity_at' in item ? item.last_activity_at ?? finished?.last_activity_at : finished?.last_activity_at)],
    ['Finished', formatSydney(finished?.finished_at ?? ('finished_at' in item ? item.finished_at : null))], ['Progress', 'progress' in item && item.progress ? `${item.progress.done} / ${item.progress.total}` : finished?.progress ? `${finished.progress.done} / ${finished.progress.total}` : 'Unavailable'],
    ['Repository', value(wiring?.repo)], ['Branch', value(wiring?.branch)], ['Head SHA', value(wiring?.head_sha)],
    ['Worktree', value(wiring?.worktree ?? ('worktree' in item ? item.worktree : null) ?? finished?.worktree)],
    ['Receipt', value('receipt' in item ? item.receipt ?? finished?.receipt ?? null : finished?.receipt ?? null)], ['Summary', value('summary' in item ? item.summary ?? finished?.summary ?? null : finished?.summary ?? null)],
    ['Blocks', waiting ? value(item.blocks) : 'Unavailable'], ['Related task', waiting ? value(item.related_task_id) : 'Unavailable'],
  ]
  const recent = snapshot.sources?.local?.items?.filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === 'object' && ('task_id' in entry || 'task' in entry) && String(('task_id' in entry ? entry.task_id : entry.task) ?? '') === item.id)).slice(-10) ?? []
  const backLabel = backBucket === 'all' ? 'All tasks' : ({ working: 'Working', waiting: 'Waiting on Ove', 'pr-gate': 'PR gate', finished: 'Finished' }[backBucket])
  return <div className="task-detail-page"><section className="panel detail-panel"><a className="back-link" href={bucketHref(backBucket)}>← Back to {backLabel}</a><div className="section-heading"><div><p className="eyebrow">{item.display_id ?? 'ID unavailable'}</p><h2>{value(numberedTitle)}</h2></div><span className="state-pill">{state}</span>{isTask(item) && item.stale ? <span className="stale-pill" title={formatSydney(item.stale.since)}>STALE · {item.stale.reason}</span> : null}</div><div className="terminal-workbench"><TerminalPanel taskId={waiting && item.related_task_id ? item.related_task_id : taskId} note={task ? item.detail ?? null : null} onLog={log} /><EventStream key={taskId} events={taskEvents} title="Task event stream" eyebrow="LIVE · WEBSOCKET + TERMINAL" scope="This task" filters={['all', 'task', 'pr', 'terminal', 'alerts']} linkTasks={false} limit={150} emptyLabel="No events for this task yet. State changes, PR gate moves and terminal lifecycle stream in here live." className="task-event-stream" /></div><dl className="detail-grid">{fields.map(([label, content]) => <div key={label}><dt>{label}</dt><dd>{value(content)}</dd></div>)}</dl><section className="detail-section"><h3>Recorded detail</h3><p className="detail-copy">{'detail' in item && item.detail ? item.detail : finished?.detail ?? 'Unavailable'}</p></section>
    <section className="detail-section"><h3>Pull request evidence</h3>{prs.length ? <ul className="detail-evidence">{prs.map((pr, index) => { const href = safeHref(pr.url); return <li key={`${pr.id}-${index}`}>{href ? <a href={href} target="_blank" rel="noreferrer">#{pr.number} · {pr.title ?? 'Pull request'}</a> : <span>#{pr.number} · {pr.title ?? 'Pull request'} (link unavailable)</span>}<p>CI: {pr.ci ?? 'Unavailable'} · {reviewLabel(pr)}</p></li> })}</ul> : <p>No linked PRs available.</p>}</section>
    <section className="detail-section"><h3>Tracker links</h3>{links.length ? <ul className="detail-evidence">{links.map((link, index) => { const href = safeHref(link.path_or_url); return <li key={`${link.label}-${index}`}>{href ? <a href={href} target="_blank" rel="noreferrer">{link.label}</a> : <span>{link.label}: Unavailable</span>}</li> })}</ul> : <p>No tracker links available.</p>}</section>
    <section className="timeline"><h3>Recorded timeline</h3>{timeline.length ? <ol>{timeline.map((event, index) => <li key={`${event.at}-${index}`}><time>{formatSydney(event.at)}</time><span>{event.state}</span></li>)}</ol> : <p>No recorded timeline events.</p>}</section>
    <section className="detail-section"><h3>Recent events</h3>{recent.length ? <ol>{recent.map((event, index) => <li key={`${String(event.at ?? event.timestamp ?? index)}-${index}`}>{String(event.at ?? event.timestamp ?? 'Timestamp unavailable')} · {String(event.event ?? event.state ?? event.type ?? 'Recorded event')}</li>)}</ol> : <p>No recent events available.</p>}</section>
{loading && <p className="subtle">Refreshing task evidence…</p>}</section></div>
}
