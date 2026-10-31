import { useMemo, useState } from 'react'
import type { FinishedItem, Snapshot, Task, WaitingItem } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { SummaryCards } from '../components/SummaryCards'
import { SourceHealth } from '../components/SourceHealth'
import { ViewState } from '../components/ViewState'
import { BlurFade } from '../components/BlurFade'
import { AnimatedShinyText } from '../components/AnimatedShinyText'
import { EventStream } from '../components/EventStream'
import { githubFreshness, idleTasks, isTask, isWaiting, observedActivity, tasksForPr, waitingRows, workingTasks } from '../lib/selectors'
import { ageLabel, formatSydney, formatSydneyClock } from '../lib/time'
import { taskHref } from '../lib/routes'
import { cardModel, snippet } from '../lib/taskCard'
import { gateReady, greptileScore } from '../lib/events'
import { useEvents } from '../lib/eventContext'
import { safeHref } from '../lib/links'

type Row = Task | WaitingItem | FinishedItem
export type StatusFilter = 'all' | 'working' | 'blocked' | 'waiting' | 'stale' | 'malformed' | 'idle'
const FILTER_LABELS: Record<StatusFilter, string> = { all: 'All current', working: 'Working', blocked: 'Blocked', waiting: 'Waiting on Ove', stale: 'Stale', malformed: 'Malformed', idle: 'Idle sessions' }

/** Rows for one overview status filter; "all current" is active work plus
 *  requests waiting on Ove (idle sessions live behind their own filter). */
export function overviewRows(snapshot: Snapshot, filter: StatusFilter): Row[] {
  const active = workingTasks(snapshot), waiting = waitingRows(snapshot).map(row => row.item), idle = idleTasks(snapshot)
  switch (filter) {
    case 'working': return active.filter(task => task.state !== 'blocked')
    case 'blocked': return active.filter(task => task.state === 'blocked')
    case 'waiting': return waiting
    case 'stale': return [...active, ...idle].filter(task => Boolean(task.stale))
    case 'malformed': return [...active, ...waiting, ...idle].filter(item => cardModel(item).malformed)
    case 'idle': return idle
    default: return [...active, ...waiting]
  }
}

function stateOf(item: Row, filter: StatusFilter): string {
  if (isWaiting(item)) return 'Waiting on Ove'
  if (filter === 'idle') return 'Idle'
  return isTask(item) ? item.state : 'done'
}

export function Overview({ snapshot, loading, error, receivedAt, refresh, live }: DashboardState & { snapshot: Snapshot }) {
  const [filter, setFilter] = useState<StatusFilter>('all')
  const { events, clear } = useEvents()
  const active = workingTasks(snapshot), waiting = waitingRows(snapshot).map(row => row.item), prs = snapshot.pr_gate ?? [], finished = snapshot.finished ?? []
  const blocked = active.filter(task => task.state === 'blocked').length
  const ready = prs.filter(pr => gateReady(pr, snapshot)).length
  const cards = [
    { label: 'Active work', value: active.length, note: `${blocked} blocked · non-terminal runner tasks`, icon: '◈', bucket: 'working' as const },
    { label: 'Blocked', value: blocked, note: 'Needs a fix or a decision', icon: '⛔', bucket: 'working' as const, href: '#/board', tone: blocked ? 'alert' as const : undefined },
    { label: 'Waiting on Ove', value: waiting.length, note: 'Recorded requests', icon: '⌁', bucket: 'waiting' as const, tone: waiting.length ? 'warn' as const : undefined },
    { label: 'PR gate', value: prs.length, note: `${ready} at 5/5 + green CI`, icon: '⇄', bucket: 'pr-gate' as const, tone: ready ? 'ok' as const : undefined },
    { label: 'Finished', value: new Set(finished.map(item => item.id)).size, note: 'Unique task identities', icon: '✓', bucket: 'finished' as const },
  ]
  const counts = useMemo(() => Object.fromEntries((Object.keys(FILTER_LABELS) as StatusFilter[]).map(key => [key, overviewRows(snapshot, key).length])) as Record<StatusFilter, number>, [snapshot])
  const rows = useMemo(() => overviewRows(snapshot, filter), [snapshot, filter])
  const activity = observedActivity(snapshot)
  const merges = snapshot.sources?.github?.command_center?.recent_merges ?? []
  const githubMeta = snapshot.sources?.github?.command_center?.meta
  const freshness = githubFreshness(snapshot)
  const partial = freshness.partial ? ['GitHub source data is degraded; local task and health data remains available'] : []
  const lastEvent = events[0]
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} sourceUpdatedAt={freshness.updatedAt} stale={freshness.stale} retry={refresh} githubMeta={githubMeta} partial={partial}><div className="view-stack overview">
    <BlurFade><section className="welcome-panel"><div><p className="eyebrow">WORKFLOW SIGNAL · EVENT DRIVEN</p><h2><AnimatedShinyText>Clarity across the work in flight.</AnimatedShinyText></h2><p>Live task evidence, source health and verified repository activity.</p></div>
      <dl className="hero-stats"><div><dt>Stream</dt><dd><span className={`connection-dot ${live ? 'is-live' : ''}`} /> {live ? 'Websocket live' : 'Polling'}</dd></div><div><dt>Last event</dt><dd>{lastEvent ? formatSydneyClock(lastEvent.at) : 'Waiting…'}</dd></div><div><dt>Snapshot</dt><dd>{formatSydneyClock(snapshot.generated_at)}</dd></div></dl></section></BlurFade>
    <SummaryCards items={cards} />
    <section className="panel overview-task-group"><div className="section-heading"><div><p className="eyebrow">CURRENT WORK</p><h2>Task details</h2></div><div className="heading-links"><a href="#/board">Board</a><a href="#/tasks">View tasks</a></div></div>
      <div className="chip-row status-filters" role="group" aria-label="Status filters">{(Object.keys(FILTER_LABELS) as StatusFilter[]).map(key => <button type="button" key={key} className={`chip chip-${key}`} aria-pressed={filter === key} aria-label={key === 'idle' ? `Idle sessions (${counts.idle})` : `${FILTER_LABELS[key]} (${counts[key]})`} aria-expanded={key === 'idle' ? filter === 'idle' : undefined} onClick={() => setFilter(current => current === key && key !== 'all' ? 'all' : key)}>{FILTER_LABELS[key]}<span>{counts[key]}</span></button>)}</div>
      {rows.length ? <ul className="overview-task-list">{rows.map(item => <OverviewRow key={`${'ask' in item ? 'wait' : 'task'}:${item.id}`} item={item} state={stateOf(item, filter)} />)}</ul> : <p className="empty-state">{filter === 'idle' ? 'No idle sessions.' : filter === 'all' ? 'No task records available.' : `No ${FILTER_LABELS[filter].toLowerCase()} tasks.`}</p>}
    </section>
    <div className="overview-columns"><EventStream events={events} onClear={clear} limit={60} className="overview-events" /><section className="panel"><div className="section-heading"><div><p className="eyebrow">MERGE GATE</p><h2>PR gate</h2></div><span className="section-count">{ready} of {prs.length} at 5/5</span></div>
      {prs.length ? <ul className="gate-list">{prs.map(pr => { const matches = tasksForPr(snapshot, pr); const task = matches.length === 1 ? matches[0]!.item : null; const href = safeHref(`https://github.com/${pr.repo}/pull/${pr.number}`); return <li key={pr.id} className={gateReady(pr, snapshot) ? 'is-ready' : ''}><span className="card-id">#{pr.number}</span><div className="gate-text">{href ? <a href={href} target="_blank" rel="noreferrer">{pr.title}</a> : <span>{pr.title}</span>}<small>{task ? <a href={taskHref(task.id)}>task {cardModel(task).taskNumber ?? cardModel(task).seq}</a> : pr.branch} · {pr.draft ? 'draft · ' : ''}{pr.merge_rule}</small></div><span className={`state-pill ci-${pr.ci}`}>CI {pr.ci}</span><span className={`state-pill ${greptileScore(pr.greptile) === 5 ? 'state-done' : ''}`}>{pr.greptile}</span></li> })}</ul> : <p className="empty-state">No PRs at the gate.</p>}</section></div>
    <div className="overview-columns"><section className="panel"><div className="section-heading"><div><p className="eyebrow">OBSERVED EVENTS</p><h2>Activity feed</h2></div><span className="section-count">Persisted observations</span></div>{activity.length ? <ol className="activity-list">{activity.slice(0, 12).map((event, i) => <li key={`${event.id}-${event.at}-${i}`}><time>{formatSydney(event.at)}</time><span>{event.label}</span><code title={event.id}>{event.id}</code></li>)}</ol> : <p className="empty-state">No persisted timeline or check events are available.</p>}</section>
      <section className="panel"><div className="section-heading"><div><p className="eyebrow">GITHUB EVIDENCE</p><h2>Recent merges</h2></div></div>{merges.length ? <ul className="merge-list">{merges.slice(0, 8).map(merge => <li key={merge.id}><div><strong>{merge.repo} #{merge.number}</strong><p>{merge.title}</p></div><time>{formatSydney(merge.merged_at)}</time></li>)}</ul> : <p className="empty-state">No verified recent merge records available.</p>}</section></div>
    <SourceHealth sources={snapshot.sources} />
  </div></ViewState>
}

function OverviewRow({ item, state }: { item: Row; state: string }) {
  const card = cardModel(item)
  const age = isWaiting(item) ? ageLabel(item.since) : isTask(item) ? ageLabel(item.last_activity_at ?? item.started_at) : 'Age unavailable'
  const stale = isTask(item) ? item.stale : null
  const pane = isTask(item) ? item.herdr_pane?.pane_id : null
  return <li className={card.malformed ? 'is-malformed' : ''}><span className="card-id" title={`Sequence ${card.seq}`}>{card.taskNumber ?? card.seq}</span>
    <div className="row-text"><a href={taskHref(item.id)} title={card.rawTitle}>{card.title ?? 'Untitled'}</a><p className={card.description ? 'row-desc' : 'row-desc is-missing'}>{card.description ? snippet(card.description, 150) : 'No description recorded'}</p>{card.malformed && <p className="malformed-flag" role="note"><span aria-hidden="true">⚠</span> Malformed — not tracked<small>{card.problems.join(' · ')}</small></p>}</div>
    <span className="row-meta">{age}{pane ? <span className="card-pane">⧉ {pane}</span> : null}</span>
    <span className="row-state"><span className={`state-pill state-${state.replaceAll(' ', '-').toLowerCase()}`}>{state}</span>{stale && <span className="stale-pill" title={formatSydney(stale.since)}>STALE · {stale.reason}</span>}</span></li>
}
