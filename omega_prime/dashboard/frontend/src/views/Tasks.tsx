import { useMemo, useState } from 'react'
import type { Snapshot } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { bucketRows, idleTasks, isTask, isWaiting, taskPrRows, tasksForPr } from '../lib/selectors'
import { ViewState } from '../components/ViewState'
import { bucketHref, taskHref, type TaskBucket } from '../lib/routes'
import { ageLabel, formatSydney } from '../lib/time'
import { safeHref } from '../lib/links'
import { reviewLabel } from '../lib/review'
import type { BucketRow } from '../lib/selectors'
import { cardModel, snippet } from '../lib/taskCard'
type Props = DashboardState & { snapshot: Snapshot; bucket: TaskBucket; notice: string | null; onTask: (id: string, kind?: 'task' | 'wait') => void }
function rowTitle(item: BucketRow['item']): string {
  if ('number' in item) return item.title
  return isWaiting(item) ? item.ask : item.title
}
const labels: Record<TaskBucket, string> = { all: 'All', waiting: 'Waiting on Ove', working: 'Working', 'pr-gate': 'PR gate', finished: 'Finished' }
const emptyCopy: Record<TaskBucket, [string, string]> = { all: ['No task records available', 'Task records will appear here when reported by a source.'], waiting: ['Nothing waiting on you', 'Choose All to view the rest of the work queue.'], working: ['No work in progress', 'Choose All to view waiting and finished work.'], 'pr-gate': ['No PRs at the gate', 'Choose All to view the rest of the work queue.'], finished: ['No finished tasks yet', 'Choose All to view current work.'] }
function bucketCountLabel(bucket: TaskBucket, count: number): string { return bucket === 'pr-gate' ? `${count} ${count === 1 ? 'PR' : 'PRs'}` : `${count} ${count === 1 ? 'task' : 'tasks'}` }
export function Tasks({ snapshot, loading, error, receivedAt, refresh, bucket, notice, onTask }: Props) {
  const [search, setSearch] = useState(''), [density, setDensity] = useState<'compact' | 'comfortable'>('compact'), [idleOpen, setIdleOpen] = useState(false)
  const rows = useMemo(() => bucketRows(snapshot, bucket), [snapshot, bucket])
  const filtered = rows.filter(({ item }) => `${rowTitle(item)} ${'display_id' in item ? item.display_id ?? '' : ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const idle = idleTasks(snapshot)
  const [emptyHeading, emptyBody] = search ? ['No task matches the current filters', 'Clear the search or choose All.'] : emptyCopy[bucket]
  const navBuckets: TaskBucket[] = ['working', 'waiting', 'pr-gate', 'finished']
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} retry={refresh}><div className="task-page">{notice && <p className="notice notice-partial" role="status">{notice}</p>}<section className="panel task-list-panel"><div className="section-heading"><div><p className="eyebrow">NUMBERED WORK QUEUE</p><h2>{labels[bucket]}</h2></div><span className="section-count">{bucketCountLabel(bucket, rows.length)} · {filtered.length} shown</span></div><nav className="bucket-controls" aria-label="Task buckets">{navBuckets.map(key => <a key={key} href={bucketHref(bucket === key ? 'all' : key)} aria-current={bucket === key ? 'page' : undefined} aria-label={bucket === key ? `${labels[key]}, ${bucketCountLabel(key, rows.length)}. Selected; activate to show All.` : labels[key]}>{labels[key]}</a>)}<a href="#/tasks" aria-current={bucket === 'all' ? 'page' : undefined}>All</a></nav><div className="task-toolbar"><label className="field-label">Search work<input aria-label="Search work" value={search} onChange={event => setSearch(event.target.value)} placeholder="Title or ID" /></label><div className="density-control" role="group" aria-label="Row density"><span>Row density</span>{(['compact', 'comfortable'] as const).map(mode => <button key={mode} aria-pressed={density === mode} onClick={() => setDensity(mode)}>{mode === 'compact' ? 'Compact' : 'Comfortable'}</button>)}</div></div>{filtered.length === 0 && <div className="empty-copy"><h3>{emptyHeading}</h3><p>{emptyBody}</p></div>}
      <ul className={`task-list task-list-${density}`}>{filtered.map(({ key, item }) => {
        if ('number' in item) {
          const source = snapshot.sources?.github?.command_center?.ci_cd?.find(row => row.kind === 'pull_request' && row.number === item.number && (!item.repo || row.repo === item.repo))
          const href = source?.url ? safeHref(source.url) : null
          const matches = tasksForPr(snapshot, item)
          const associatedTask = matches.length === 1 ? matches[0]?.item : null
          return <li key={key} className="task-row pr-gate-row"><span className="task-number">PR #{item.number}</span><strong>{item.title}</strong><span className="state-pill">{item.ci}</span>{href && <a href={href} target="_blank" rel="noreferrer">Open PR</a>}{associatedTask && <a href={taskHref(associatedTask.id)} onClick={() => onTask(associatedTask.id)}>{associatedTask.display_id ?? 'Open associated task'}</a>}<span className="task-evidence">{source ? reviewLabel(source) : 'Greptile review unavailable'}</span></li>
        }
        const title = rowTitle(item)
        const status = isWaiting(item) ? 'Waiting on Ove' : isTask(item) ? item.state : ('state' in item ? item.state : null) ?? ('result' in item && item.result ? `Finished · ${item.result}` : 'Finished')
        const task = isTask(item) ? item : null
        const prs = task ? taskPrRows(snapshot, task) : []
        const pr = prs.length === 1 ? prs[0] : undefined
        const age = isWaiting(item) ? ageLabel(item.since) : isTask(item) ? ageLabel(item.last_activity_at ?? item.started_at) : ageLabel(item.finished_at)
        const card = cardModel(item)
        return <li key={key} className={`task-row task-row-${density} ${card.malformed ? 'is-malformed' : ''}`}><span className="task-number" title={item.display_id ?? undefined}>{card.taskNumber ?? card.seq}</span><span className="task-title-cell"><a className="task-title-link" href={taskHref(item.id)} onClick={() => onTask(item.id, isWaiting(item) ? 'wait' : 'task')} aria-label={`${item.display_id ?? 'ID unavailable'} ${title}`}>{card.title ?? title}</a><small className={card.description ? 'row-desc' : 'row-desc is-missing'}>{card.description ? snippet(card.description, density === 'compact' ? 110 : 220) : 'No description recorded'}</small>{card.malformed && <small className="malformed-flag" role="note">⚠ Malformed — not tracked · {card.problems.join(' · ')}</small>}</span><span className="task-state"><span className="state-pill">{status}</span>{task?.stale ? <span className="stale-pill" title={formatSydney(task.stale.since)}>STALE · {task.stale.reason}</span> : null}</span><span className="task-age">{age}</span><span>{task ? task.owner?.label ?? task.owner?.ref ?? task.owner?.kind ?? 'Unavailable' : 'Unavailable'}</span><span className="task-pr">{pr?.url && safeHref(pr.url) ? <a href={safeHref(pr.url) ?? undefined} target="_blank" rel="noreferrer">PR #{pr.number}</a> : 'PR unavailable'} · {pr?.ci ?? 'CI unavailable'}</span><span className="task-evidence">{pr ? reviewLabel(pr) : 'Greptile review unavailable'}</span></li>
      })}</ul></section>{bucket === 'all' && <section className="panel idle-group"><button className="idle-disclosure" aria-expanded={idleOpen} onClick={() => setIdleOpen(open => !open)}>Idle sessions ({idle.length})</button>{idleOpen && (idle.length ? <ul className="task-list task-list-compact">{idle.map(item => <li key={item.id} className="task-row task-row-compact"><span className="task-number">{item.display_id ?? 'ID unavailable'}</span><a className="task-title-link" href={taskHref(item.id)} onClick={() => onTask(item.id)}>{item.title}</a><span className="task-state"><span className="state-pill">Idle</span>{item.stale ? <span className="stale-pill" title={formatSydney(item.stale.since)}>STALE · {item.stale.reason}</span> : null}</span><span>{ageLabel(item.last_activity_at ?? item.started_at)}</span><span>{item.owner?.label ?? item.owner?.ref ?? item.owner?.kind ?? 'Unavailable'}</span></li>)}</ul> : <p>No idle sessions.</p>)}</section>}</div></ViewState>
}
