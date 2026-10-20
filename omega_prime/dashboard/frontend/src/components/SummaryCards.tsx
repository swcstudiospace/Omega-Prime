import type { ReactNode } from 'react'
import { NumberTicker } from './NumberTicker'
import { BorderBeam } from './BorderBeam'
type Item = { label: string; value: number; note: string; icon: ReactNode }
export function SummaryCards({ items }: { items: Item[] }) {
  return <div className="summary-grid">{items.map(item => <article className="summary-card" key={item.label}><BorderBeam /><span className="card-icon" aria-hidden="true">{item.icon}</span><p>{item.label}</p><strong><NumberTicker value={item.value} /></strong><small>{item.note}</small></article>)}</div>
}
