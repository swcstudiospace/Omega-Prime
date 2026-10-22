import { useMemo, useState } from 'react'
import { categoryOf, filterEvents, payloadSummary, type DashEvent, type EventFilter } from '../lib/events'
import { formatSydneyClock } from '../lib/time'
import { taskHref } from '../lib/routes'

const FILTERS: Array<{ key: EventFilter; label: string }> = [{ key: 'all', label: 'All' }, { key: 'task', label: 'Tasks' }, { key: 'pr', label: 'PRs' }, { key: 'terminal', label: 'Terminal' }, { key: 'system', label: 'System' }, { key: 'alerts', label: 'Alerts' }]

type Props = {
  events: DashEvent[]
  title?: string
  eyebrow?: string
  /** Scope note shown in the header, e.g. "This task". */
  scope?: string
  /** Hide the filter chips that cannot match in this scope. */
  filters?: EventFilter[]
  /** Link each row to its task (off inside a task's own detail page). */
  linkTasks?: boolean
  limit?: number
  onClear?: () => void
  emptyLabel?: string
  className?: string
}

/** Structured, live event log: Sydney timestamp, severity, type, source and
 *  payload summary per row, with category filters, search and pause. */
export function EventStream({ events, title = 'Event stream', eyebrow = 'LIVE · WEBSOCKET', scope, filters, linkTasks = true, limit = 200, onClear, emptyLabel = 'No events yet. Changes stream in here as the websocket reports them.', className }: Props) {
  const [filter, setFilter] = useState<EventFilter>('all')
  const [query, setQuery] = useState('')
  const [frozen, setFrozen] = useState<DashEvent[] | null>(null)
  const source = frozen ?? events
  const shown = useMemo(() => filterEvents(source, filter, query).slice(0, limit), [source, filter, query, limit])
  // Count arrivals by identity, not length: once the log sits at its cap new
  // events evict old ones and the length stops changing.
  const pending = frozen ? newerThan(events, frozen[0]?.id ?? null) : 0
  const chips = FILTERS.filter(item => !filters || filters.includes(item.key))
  const count = (key: EventFilter) => filterEvents(source, key).length
  return <section className={`panel event-stream ${className ?? ''}`} aria-labelledby={`${title.replaceAll(' ', '-')}-heading`}>
    <div className="section-heading"><div><p className="eyebrow">{eyebrow}{scope ? ` · ${scope}` : ''}</p><h2 id={`${title.replaceAll(' ', '-')}-heading`}>{title}</h2></div>
      <div className="event-actions"><span className="section-count" aria-live="polite">{shown.length} of {source.length}</span>
        <button type="button" className="quiet-button" aria-pressed={frozen !== null} onClick={() => setFrozen(current => current ? null : events)}>{frozen ? `Resume${pending > 0 ? ` (${pending} new)` : ''}` : 'Pause'}</button>
        {onClear && <button type="button" className="quiet-button" onClick={() => { setFrozen(null); onClear() }} disabled={events.length === 0}>Clear</button>}</div></div>
    <div className="event-toolbar"><div className="chip-row" role="group" aria-label="Event filters">{chips.map(item => <button type="button" key={item.key} className="chip" aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>{item.label}<span>{count(item.key)}</span></button>)}</div>
      <input className="event-search" type="search" aria-label="Search events" placeholder="Filter events" value={query} onChange={event => setQuery(event.target.value)} /></div>
    {shown.length === 0 ? <p className="empty-state event-empty">{source.length === 0 ? emptyLabel : 'No events match the current filter.'}</p>
      : <ol className="event-list" aria-label={`${title} entries`}>{shown.map(event => <EventRow key={event.id} event={event} linkTask={linkTasks} />)}</ol>}
  </section>
}

/** Events (newest first) that arrived after `anchorId`; every event when the
 *  anchor is absent or has already been evicted from the bounded log. */
export function newerThan(events: DashEvent[], anchorId: string | null): number {
  if (anchorId === null) return events.length
  const index = events.findIndex(event => event.id === anchorId)
  return index === -1 ? events.length : index
}

function EventRow({ event, linkTask }: { event: DashEvent; linkTask: boolean }) {
  const payload = payloadSummary(event.payload)
  const keys = Object.keys(event.payload).length
  return <li className={`event-row sev-${event.severity}`} data-category={categoryOf(event.type)}>
    <time dateTime={event.at} title={event.at}>{formatSydneyClock(event.at)}</time>
    <span className="event-dot" aria-label={event.severity} />
    <code className="event-type">{event.type}</code>
    <span className={`event-source src-${event.source}`}>{event.source}</span>
    <div className="event-text"><p><strong>{event.title}</strong> <span>{event.summary}</span>{linkTask && event.taskId && <> · <a href={taskHref(event.taskId)}>open</a></>}</p>
      {keys > 0 && <details><summary>{payload || `${keys} fields`}</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}</div>
  </li>
}
