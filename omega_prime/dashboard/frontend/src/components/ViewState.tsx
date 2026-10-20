import type { ReactNode } from 'react'
import { ageLabel } from '../lib/time'
type Props = { loading: boolean; error: string | null; receivedAt: number | null; sourceUpdatedAt?: string | null; stale?: boolean; partial?: string[]; retry: () => void; children: ReactNode; empty?: boolean; emptyLabel?: string }
export function ViewState({ loading, error, receivedAt, sourceUpdatedAt, stale = false, partial = [], retry, children, empty = false, emptyLabel = 'No records found.' }: Props) {
  if (loading && !receivedAt) return <div className="state-grid" aria-label="Loading dashboard"><div className="skeleton skeleton-wide" /><div className="skeleton" /><div className="skeleton" /><div className="skeleton skeleton-wide" /></div>
  const age = sourceUpdatedAt ? ageLabel(sourceUpdatedAt) : receivedAt !== null ? ageLabel(new Date(receivedAt).toISOString()) : 'Age unavailable'
  return <div className="view-state">{error && <div className="notice notice-error" role="alert"><span>{error}</span><button onClick={retry}>Retry</button></div>}{receivedAt !== null && <p className="freshness">Snapshot fetched {age}{stale ? ' · stale' : ''}</p>}{partial.map(reason => <p className="notice notice-partial" key={reason}>Partial source data · {reason}</p>)}{empty && <p className="empty-state">{emptyLabel}</p>}{children}</div>
}
