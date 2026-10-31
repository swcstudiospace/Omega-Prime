import type { ReactNode } from 'react'
import type { GitHubMeta } from '../types'
import { ageLabel, formatSydney } from '../lib/time'
type Props = { loading: boolean; error: string | null; receivedAt: number | null; sourceUpdatedAt?: string | null; stale?: boolean; partial?: string[]; githubMeta?: GitHubMeta | null; retry: () => void; children: ReactNode; empty?: boolean; emptyLabel?: string }
export function ViewState({ loading, error, receivedAt, sourceUpdatedAt, stale = false, partial = [], githubMeta, retry, children, empty = false, emptyLabel = 'No records found.' }: Props) {
  if (loading && !receivedAt) return <div className="state-grid" aria-label="Loading dashboard"><div className="skeleton skeleton-wide" /><div className="skeleton" /><div className="skeleton" /><div className="skeleton skeleton-wide" /></div>
  const age = sourceUpdatedAt ? ageLabel(sourceUpdatedAt) : receivedAt !== null ? ageLabel(new Date(receivedAt).toISOString()) : 'Age unavailable'
  const sections = Object.entries(githubMeta?.sections ?? {}).filter(([, section]) => Boolean(section.partial || section.stale))
  const hasSectionEvidence = sections.length > 0
  return <div className="view-state">{error && <div className="notice notice-error" role="alert"><span>{error}</span><button onClick={retry}>Retry</button></div>}{receivedAt !== null && <p className="freshness">{sourceUpdatedAt ? 'Source refreshed' : 'Snapshot fetched'} {age}{stale ? ' · stale' : ''}</p>}{sections.map(([name, section]) => <p className="notice notice-partial" role="status" key={name}>Partial source data · {name}: {section.reason ?? 'Reason unavailable'}.{section.stale ? ' Showing last successful data · stale.' : ''} Previous successful fetch: {section.fetched_at ? formatSydney(section.fetched_at) : 'Unavailable'}.</p>)}{!hasSectionEvidence && partial.map(reason => <p className="notice notice-partial" key={reason}>Partial source data · {reason}</p>)}{empty && <p className="empty-state">{emptyLabel}</p>}{children}</div>
}
