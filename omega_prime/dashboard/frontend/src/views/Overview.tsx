import type { Snapshot } from '../types'
import type { DashboardState } from '../hooks/useDashboard'
import { SummaryCards } from '../components/SummaryCards'
import { SourceHealth } from '../components/SourceHealth'
import { ViewState } from '../components/ViewState'
import { BlurFade } from '../components/BlurFade'
import { AnimatedShinyText } from '../components/AnimatedShinyText'
import { githubFreshness, observedActivity } from '../lib/selectors'
import { formatSydney } from '../lib/time'
export function Overview({ snapshot, loading, error, receivedAt, refresh }: DashboardState & { snapshot: Snapshot }) {
  const active = snapshot.active ?? [], waiting = snapshot.waiting_on_ove ?? [], prs = snapshot.pr_gate ?? [], finished = snapshot.finished ?? []
  const cards = [{ label: 'Active work', value: active.filter(item => !['done', 'complete', 'finished'].includes(item.state.toLowerCase())).length, note: 'Non-terminal runner tasks', icon: '◈' }, { label: 'Waiting on Ove', value: waiting.length, note: 'Recorded requests', icon: '⌁' }, { label: 'PR gate', value: prs.length, note: 'Open merge checks', icon: '⇄' }, { label: 'Finished', value: new Set(finished.map(item => item.id)).size, note: 'Unique task identities', icon: '✓' }]
  const activity = observedActivity(snapshot)
  const merges = snapshot.sources?.github?.extra?.command_center?.recent_merges ?? []
  const freshness = githubFreshness(snapshot)
  const partial = freshness.partial ? ['GitHub source data is degraded; local task and health data remains available'] : []
  return <ViewState loading={loading} error={error} receivedAt={receivedAt} sourceUpdatedAt={freshness.updatedAt} stale={freshness.stale} retry={refresh} partial={partial}><div className="view-stack"><BlurFade><section className="welcome-panel"><p className="eyebrow">WORKFLOW SIGNAL · READ ONLY</p><h2><AnimatedShinyText>Clarity across the work in flight.</AnimatedShinyText></h2><p>Live task evidence, source health and verified repository activity.</p></section></BlurFade><SummaryCards items={cards} /><div className="overview-columns"><section className="panel"><div className="section-heading"><div><p className="eyebrow">OBSERVED EVENTS</p><h2>Activity feed</h2></div><span className="section-count">Persisted observations</span></div>{activity.length ? <ol className="activity-list">{activity.slice(0, 12).map((event, i) => <li key={`${event.id}-${event.at}-${i}`}><time>{formatSydney(event.at)}</time><span>{event.label}</span><code>{event.id}</code></li>)}</ol> : <p className="empty-state">No persisted timeline or check events are available.</p>}</section><section className="panel"><div className="section-heading"><div><p className="eyebrow">GITHUB EVIDENCE</p><h2>Recent merges</h2></div></div>{merges.length ? <ul className="merge-list">{merges.slice(0, 8).map(merge => <li key={merge.id}><div><strong>{merge.repo} #{merge.number}</strong><p>{merge.title}</p></div><time>{formatSydney(merge.merged_at)}</time></li>)}</ul> : <p className="empty-state">No verified recent merge records available.</p>}</section></div><SourceHealth sources={snapshot.sources} /></div></ViewState>
}
