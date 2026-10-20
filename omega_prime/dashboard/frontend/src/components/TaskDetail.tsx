import type { FinishedItem, Task, WaitingItem } from '../types'
import { formatSydney } from '../lib/time'
import { safeHref } from '../lib/links'
type Props = { item: Task | WaitingItem | FinishedItem | null }
export function TaskDetail({ item }: Props) {
  if (!item) return <section className="panel detail-panel"><p className="empty-state">Select a task to inspect its available evidence.</p></section>
  const active = 'state' in item
  const waiting = 'ask' in item
  const timeline = item.timeline ?? []
  const title = active ? item.title : waiting ? item.ask : item.title
  const fields: Array<[string, string]> = active ? [['Owner', item.owner?.label ?? 'Unavailable'], ['Source', item.source], ['State', item.state], ['Model', item.model ?? 'Unavailable'], ['Started', formatSydney(item.started_at)], ['Last activity', formatSydney(item.last_activity_at)], ['Progress', item.progress ? `${item.progress.done} / ${item.progress.total}` : 'Unavailable']] : waiting ? [['State', 'Waiting on Ove'], ['Blocks', item.blocks || 'Unavailable'], ['Related task', item.related_task_id ?? 'Unavailable'], ['Since', formatSydney(item.since)], ['Owner', 'Unavailable'], ['Model', 'Unavailable'], ['Progress', 'Unavailable']] : [['State', 'Finished'], ['Result', item.result], ['Finished', formatSydney(item.finished_at)], ['Receipt', item.receipt ?? 'Unavailable'], ['Summary', item.summary ?? 'Unavailable'], ['Owner', 'Unavailable'], ['Model', 'Unavailable']]
  const links = active ? item.links ?? [] : []
  return <section className="panel detail-panel"><div className="section-heading"><div><p className="eyebrow">{item.display_id ?? 'ID unavailable'}</p><h2>{title}</h2></div></div><dl className="detail-grid">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{active && item.detail && <p className="detail-copy">{item.detail}</p>}{links.length > 0 && <div className="detail-links">{links.map((link, i) => { const href = safeHref(link.url); return href ? <a key={`${link.label}-${i}`} href={href} target="_blank" rel="noreferrer">{link.label}</a> : <span key={`${link.label}-${i}`}>{link.label}: {link.url}</span> })}</div>}<section className="timeline"><h3>Observed timeline</h3>{timeline.length ? <ol>{timeline.map((event, i) => <li key={`${event.at}-${i}`}><time>{formatSydney(event.at)}</time><span>{event.state}</span></li>)}</ol> : <p className="subtle">No persisted timeline observations are available.</p>}</section></section>
}
