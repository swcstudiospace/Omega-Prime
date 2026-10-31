import type { FinishedItem, Task, WaitingItem } from '../types'
import { cardModel, snippet } from '../lib/taskCard'
import { taskHref } from '../lib/routes'
import { ageLabel } from '../lib/time'
import { isTask, isWaiting } from '../lib/selectors'

type Props = { item: Task | WaitingItem | FinishedItem; state?: string; onOpen?: (id: string) => void; compact?: boolean }

/** One task as a card: 000NN id, title and description. A row that breaks
 *  the tracking rule renders a "malformed — not tracked" state instead of
 *  being hidden; the record itself is never altered. */
export function TaskCard({ item, state, onOpen, compact = false }: Props) {
  const card = cardModel(item)
  const age = isWaiting(item) ? ageLabel(item.since) : isTask(item) ? ageLabel(item.last_activity_at ?? item.started_at) : ageLabel('finished_at' in item ? item.finished_at : null)
  const pane = isTask(item) ? item.herdr_pane?.pane_id ?? null : null
  const stale = isTask(item) ? item.stale ?? null : null
  const label = `${card.taskNumber ?? card.seq} ${card.title ?? card.rawTitle ?? 'Untitled'}`
  return <article className={`task-card ${card.malformed ? 'is-malformed' : ''} ${compact ? 'is-compact' : ''}`} data-state={state ?? (isTask(item) ? item.state : undefined)}>
    <header><span className="card-id" title={`Sequence ${card.seq}`}>{card.taskNumber ?? card.seq}</span>{state && <span className={`state-pill state-${state.replaceAll(' ', '-').toLowerCase()}`}>{state}</span>}{stale && <span className="stale-pill" title={stale.since}>STALE · {stale.reason}</span>}</header>
    <a className="card-title" href={taskHref(item.id)} onClick={() => onOpen?.(item.id)} aria-label={label} title={card.rawTitle}>{card.title ?? <em>Untitled</em>}</a>
    {card.description ? <p className="card-desc">{snippet(card.description, compact ? 110 : 160)}</p> : <p className="card-desc is-missing">No description recorded</p>}
    {card.malformed && <p className="malformed-flag" role="note"><span aria-hidden="true">⚠</span> Malformed — not tracked<span className="sr-only">: </span><small>{card.problems.join(' · ')}</small></p>}
    <footer><span>#{card.seq}</span><span>{age}</span>{pane && <span className="card-pane" title="Herdr pane">⧉ {pane}</span>}</footer>
  </article>
}
