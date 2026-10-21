import type { Snapshot, CiRow } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { ViewState } from '../components/ViewState'
import { formatSydney } from '../lib/time'
import { safeHref } from '../lib/links'
import { correlateWiring, githubFreshness } from '../lib/selectors'

export function CiCd({ snapshot, loading, error, receivedAt, refresh }: DashboardState & { snapshot: Snapshot }) {
  const cc = snapshot.sources?.github?.command_center
  const detailedRows = cc?.ci_cd ?? []
  const backendBranches = new Set<string>()
  const seenBranches = new Set<string>()
  const seenPullRequests = new Set<string>()
  const rows: CiRow[] = []
  for (const row of detailedRows) {
    if (row.kind === 'branch' && row.repo && row.branch) {
      const identity = JSON.stringify([row.repo, row.branch])
      backendBranches.add(identity)
      if (seenBranches.has(identity)) continue
      seenBranches.add(identity)
    } else if (row.kind === 'pull_request' && row.repo) {
      const pullRequest = row.number != null ? `number:${row.number}` : row.id ? `id:${row.id}` : null
      if (pullRequest) {
        const identity = JSON.stringify([row.repo, pullRequest])
        if (seenPullRequests.has(identity)) continue
        seenPullRequests.add(identity)
      }
    }
    rows.push(row)
  }
  for (const repo of cc?.repositories ?? []) {
    const latest = repo.latest_main_ci
    if (!latest) continue
    const branch = latest.branch ?? repo.default_branch ?? 'default branch'
    if (backendBranches.has(JSON.stringify([repo.full_name, branch]))) continue
    rows.push({
      id: `repo:${repo.id}:default-ci`, kind: 'branch', repo: repo.full_name, branch,
      head_sha: latest.head_sha ?? null, ci: latest.ci ?? null, checks: latest.checks ?? [],
    })
  }
  const localTasks = [...(snapshot.active ?? []), ...(snapshot.finished ?? [])]
  const meta = cc?.meta
  const freshness = githubFreshness(snapshot)
  const partial = meta?.partial ? [meta.reason ?? 'Some GitHub data is partial'] : freshness.partial ? ['GitHub source is degraded; local data remains available'] : []
  const summary = detailedRows.length ? null : (snapshot.pr_gate ?? [])
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} sourceUpdatedAt={freshness.updatedAt} stale={freshness.stale} retry={refresh} partial={partial} empty={!rows.length && !summary?.length} emptyLabel={cc ? 'No CI/CD records are available.' : 'GitHub command-center data is not configured or has not been collected.'}>
    <div className="view-stack">
      {summary?.length ? <section className="panel"><h2>Legacy merge gate · detailed records unavailable</h2><ul className="merge-list">{summary.map(pr => <li key={pr.id}><strong>{pr.repo} #{pr.number} · {pr.title}</strong><span>{pr.ci} · {pr.merge_rule}</span></li>)}</ul></section> : null}
      {rows.map(row => <CiCard key={cardKey(row)} row={row} tasks={localTasks} />)}
    </div>
  </ViewState>
}
function cardKey(row: CiRow): string {
  if (row.kind === 'branch' && row.repo && row.branch) return `branch:${JSON.stringify([row.repo, row.branch])}`
  if (row.kind === 'pull_request') return `pull-request:${JSON.stringify([row.repo ?? '', row.number != null ? `number:${row.number}` : `id:${row.id}`])}`
  return `${row.kind}:${row.id}`
}

function CiCard({ row, tasks }: { row: CiRow; tasks: Array<NonNullable<Snapshot['active']>[number] | NonNullable<Snapshot['idle_sessions']>[number] | NonNullable<Snapshot['finished']>[number]> }) {
  const correlation = correlateWiring(row, tasks)
  const review = row.greptile_review
  const mergedPrNumber = review && 'pr_number' in review && typeof review.pr_number === 'number' ? review.pr_number : null
  const evidenceSource = review && 'evidence_source' in review && typeof review.evidence_source === 'string' ? review.evidence_source : review?.source ?? 'Review source unavailable'
  const reviewLabel = review?.source === 'not_applicable' ? 'No PR review for this commit'
    : review?.source === 'unavailable' || !review ? 'Greptile review unavailable'
      : review.source === 'merged_pr' && mergedPrNumber !== null && review.score != null ? `Greptile ${review.score}/5 · from #${mergedPrNumber}`
        : review.score != null && !review.reviewed_sha ? `Greptile ${review.score}/5 · reviewed SHA not reported`
          : `${review.score != null ? `Score ${review.score}` : 'Score unavailable'} · ${evidenceSource} · reviewed SHA ${review.reviewed_sha?.slice(0, 12) ?? 'not reported'} · ${review.matches_head == null ? 'head match unavailable' : review.matches_head ? 'matches current head' : 'does not match current head'}`
  const sourceHref = safeHref(row.url)
  return <article className="panel ci-card">
    <div className="section-heading"><div><p className="eyebrow">{row.kind.replaceAll('_', ' ')} · {row.repo ?? 'Repository unavailable'}</p><h2>{row.number ? `#${row.number} · ` : ''}{row.title ?? row.branch ?? 'CI record'}</h2></div>{sourceHref && <a href={sourceHref} target="_blank" rel="noreferrer">Open source ↗</a>}</div>
    <div className="ci-metadata"><span>Branch <code>{row.branch ?? 'Unavailable'}</code></span><span>Head <code>{row.head_sha?.slice(0, 12) ?? 'Unavailable'}</code></span><span>CI <b>{row.ci ?? 'Unknown'}</b></span>{row.draft && <span className="state-pill">Draft</span>}</div>
    <h3>Checks</h3>
    {row.checks?.length ? <ul className="check-list">{row.checks.map((check, i) => {
      const duration = check.duration_s ?? (check.started_at && check.completed_at ? Math.max(0, Math.round((Date.parse(check.completed_at) - Date.parse(check.started_at)) / 1000)) : null)
      const href = safeHref(check.details_url)
      return <li key={check.id ?? `${check.name}-${i}`}><span className={`health-dot ${check.conclusion === 'success' ? 'healthy' : check.conclusion === 'failure' ? 'degraded' : ''}`} /><strong>{check.name}</strong><span>{check.conclusion ?? check.status}</span><span>{duration !== null ? `${duration}s` : check.completed_at ? formatSydney(check.completed_at) : 'Duration pending'}</span>{href && <a href={href} target="_blank" rel="noreferrer">Details</a>}</li>
    })}</ul> : <p className="subtle">Detailed checks unavailable.</p>}
    <div className="evidence-row"><div><h3>Greptile review</h3><p>{review?.score != null ? `Score ${review.score}` : 'Score unknown'} · {review?.source ?? 'Review source unknown'} · reviewed SHA {review?.reviewed_sha?.slice(0, 12) ?? 'unknown'} · {review?.matches_head == null ? 'head match unknown' : review.matches_head ? 'matches current head' : 'does not match current head'}</p>{safeHref(review?.url) && <a href={safeHref(review?.url) ?? undefined} target="_blank" rel="noreferrer">Review evidence ↗</a>}</div><div><h3>Wiring evidence</h3>{correlation.length ? correlation.map(task => <p key={task.id}>{task.display_id ?? 'ID unavailable'} · {task.wiring?.phase ?? 'Phase unknown'} · {task.wiring?.stage ?? 'Stage unknown'} · merge order unavailable in snapshot{!row.repo ? ' · repository provenance unavailable' : ''}</p>) : <p>Unknown · no unique branch/SHA corroboration.</p>}</div></div>
  </article>
}
