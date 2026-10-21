import type { ReactNode } from 'react'
import { NumberTicker } from './NumberTicker'
import { BorderBeam } from './BorderBeam'
import { bucketHref } from '../lib/routes'
import type { TaskBucket } from '../lib/routes'
type Item = { label: string; value: number; note: string; icon: ReactNode; bucket: TaskBucket }
export function SummaryCards({ items }: { items: Item[] }) {
  return <div className="summary-grid">{items.map(item => <a href={bucketHref(item.bucket)} className="summary-card" key={item.label} aria-label={`View ${item.bucket === 'working' ? 'Working' : item.label} tasks, ${item.value}`}><BorderBeam /><span className="card-icon" aria-hidden="true">{item.icon}</span><p>{item.label}</p><strong><NumberTicker value={item.value} /></strong><small>{item.note}</small></a>)}</div>
}
