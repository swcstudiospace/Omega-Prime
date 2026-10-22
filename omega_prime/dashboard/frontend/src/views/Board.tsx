import { useState } from 'react'
import type { Snapshot } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { ViewState } from '../components/ViewState'
import { TaskCard } from '../components/TaskCard'
import { boardColumns } from '../lib/board'
import { bucketHref, taskHref } from '../lib/routes'
import { safeHref } from '../lib/links'
import { tasksForPr, isTask, isWaiting } from '../lib/selectors'
import { cardModel, finishedState } from '../lib/taskCard'
import { greptileScore, gateReady } from '../lib/events'

const COLUMN_CAP = 25
type Props = DashboardState & { snapshot: Snapshot; onTask: (id: string) => void }

export function Board({ snapshot, loading, error, receivedAt, refresh, onTask }: Props) {
  const [showMalformed, setShowMalformed] = useState(true)
  const columns = boardColumns(snapshot)
  const malformed = columns.flatMap(column => column.key === 'pr-gate' ? [] : column.items.filter(item => cardModel(item).malformed)).length
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} retry={refresh}><div className="board-page">
    <div className="board-toolbar"><p className="subtle">Cards open the task detail with its live Herdr terminal and event stream. Display only — task records are never edited here.</p>
      <label className="toggle"><input type="checkbox" checked={showMalformed} onChange={event => setShowMalformed(event.target.checked)} /> Show malformed rows <span className="malformed-count">{malformed}</span></label></div>
    <div className="board" role="group" aria-label="Kanban board">{columns.map(column => {
      const taskItems = column.key === 'pr-gate' ? [] : column.items.filter(item => showMalformed || !cardModel(item).malformed)
      const items: unknown[] = column.key === 'pr-gate' ? column.items : taskItems
      // Only a column with its own list page is capped (the rest stay one
      // click away); Blocked has none, so every blocked card stays on the board.
      const cap = column.bucket ? COLUMN_CAP : Number.POSITIVE_INFINITY
      return <section key={column.key} className={`board-column col-${column.key}`} aria-labelledby={`col-${column.key}`}>
        <header><h2 id={`col-${column.key}`}>{column.label}</h2><span className="column-count">{items.length}</span>{column.bucket && <a href={bucketHref(column.bucket)} aria-label={`Open ${column.label} list`}>List ↗</a>}</header>
        <div className="column-cards">{items.length === 0 ? <p className="column-empty">Nothing here</p> : column.key === 'pr-gate'
          ? column.items.slice(0, cap).map(pr => {
            const matches = tasksForPr(snapshot, pr)
            const task = matches.length === 1 ? matches[0]!.item : null
            const source = snapshot.sources?.github?.command_center?.ci_cd?.find(row => row.kind === 'pull_request' && row.number === pr.number && row.repo === pr.repo)
            const href = task ? taskHref(task.id) : safeHref(source?.url ?? `https://github.com/${pr.repo}/pull/${pr.number}`)
            const ready = gateReady(pr, snapshot)
            return <article key={pr.id} className={`task-card pr-card ${ready ? 'is-ready' : ''}`}>
              <header><span className="card-id">PR #{pr.number}</span>{pr.draft && <span className="state-pill">draft</span>}<span className={`state-pill ci-${pr.ci}`}>CI {pr.ci}</span><span className={`state-pill ${greptileScore(pr.greptile) === 5 ? 'state-done' : ''}`}>Greptile {pr.greptile}</span></header>
              {href ? <a className="card-title" href={href} {...(task ? { onClick: () => onTask(task.id) } : { target: '_blank', rel: 'noreferrer' })}>{pr.title}</a> : <span className="card-title">{pr.title}</span>}
              <p className="card-desc">{task ? `Task ${cardModel(task).taskNumber ?? cardModel(task).seq} · ${cardModel(task).title ?? ''}` : `${pr.branch} · no unique task link`}</p>
              <footer><span>{pr.repo.split('/').pop()}</span><span>{ready ? (pr.draft ? 'gate 5/5 · held (draft)' : 'gate 5/5') : pr.merge_rule}</span></footer>
            </article>
          })
          : taskItems.slice(0, cap).map(item => <TaskCard key={item.id} item={item} compact state={column.key === 'waiting' ? 'Waiting on Ove' : column.key === 'finished' && !isWaiting(item) ? finishedState(item) : isTask(item) ? item.state : undefined} onOpen={onTask} />)}
          {items.length > cap && column.bucket && <a className="column-more" href={bucketHref(column.bucket)}>+{items.length - cap} more</a>}</div>
      </section>
    })}</div>
  </div></ViewState>
}
