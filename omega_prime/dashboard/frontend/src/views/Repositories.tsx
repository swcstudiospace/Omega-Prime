import { useMemo, useState } from 'react'
import type { Snapshot } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { githubFreshness, sortRepositories } from '../lib/selectors'
import { safeHref } from '../lib/links'
import { ViewState } from '../components/ViewState'
import { formatSydney } from '../lib/time'
export function Repositories({ snapshot, loading, error, receivedAt, refresh }: DashboardState & { snapshot: Snapshot }) {
  const [query, setQuery] = useState(''), [sort, setSort] = useState<'name' | 'pushed' | 'visibility'>('name')
  const cc = snapshot.sources?.github?.extra?.command_center
  const repos = useMemo(() => sortRepositories(cc?.repositories ?? [], query, sort), [cc?.repositories, query, sort])
  const freshness = githubFreshness(snapshot)
  const partial = cc?.meta?.partial ? [cc.meta.reason ?? 'GitHub inventory is partial'] : freshness.partial ? ['GitHub source is degraded; local views remain available'] : []
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} sourceUpdatedAt={freshness.updatedAt} stale={freshness.stale} retry={refresh} partial={partial} empty={!repos.length} emptyLabel={cc ? query ? 'No repositories match this search.' : 'No repository records returned.' : 'Repository inventory is unavailable or not configured.'}><section className="panel"><div className="section-heading"><div><p className="eyebrow">SWCSTUDIOSPACE INVENTORY</p><h2>Repositories</h2></div><span className="section-count">{repos.length} found</span></div><div className="repo-controls"><label className="field-label">Search repositories<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Name or language" /></label><label className="field-label">Sort<select value={sort} onChange={event => setSort(event.target.value as typeof sort)}><option value="name">Name</option><option value="pushed">Recently pushed</option><option value="visibility">Visibility</option></select></label></div><div className="repo-grid">{repos.map(repo => <article className="repo-card" key={`${repo.full_name}-${repo.id}`}><div><p className="eyebrow">{repo.visibility ?? 'Visibility unknown'}</p><h3>{repo.full_name}</h3></div><p>{repo.language ?? 'Language unknown'} · {repo.default_branch ?? 'Default branch unknown'}</p><p>Open PRs: {repo.open_pr_count ?? 'Unknown'} · Pushed {formatSydney(repo.pushed_at)}</p><p>Default branch CI: {repo.latest_main_ci?.ci ?? 'Unknown'}</p>{repo.url && safeHref(repo.url) && <a href={safeHref(repo.url) ?? undefined} target="_blank" rel="noreferrer">View repository ↗</a>}</article>)}</div></section></ViewState>
}
