import type { CiRow, FinishedItem, PrRow, Repository, Snapshot, Task, WaitingItem } from '../types'
export type TaskSelection = { key: string; item: Task | WaitingItem | FinishedItem }
export function isTask(item: Task | WaitingItem | FinishedItem): item is Task { return !('result' in item) && 'source' in item && typeof item.source === 'string' }
export function isWaiting(item: Task | WaitingItem | FinishedItem): item is WaitingItem { return 'ask' in item }
export function taskSelection(snapshot: Snapshot): TaskSelection[] {
  const found = new Map<string, TaskSelection>()
  for (const item of snapshot.active ?? []) found.set(`task:${item.id}`, { key: `task:${item.id}`, item })
  for (const item of snapshot.idle_sessions ?? []) found.set(`task:${item.id}`, { key: `task:${item.id}`, item })
  for (const item of snapshot.finished ?? []) {
    const key = `task:${item.id}`, current = found.get(key)
    if (current && isTask(current.item)) {
      const prior = current.item
      found.set(key, { key, item: { ...prior, detail: prior.detail ?? item.detail ?? item.summary ?? item.result, source: prior.source ?? item.source, owner: prior.owner ?? item.owner, model: prior.model ?? item.model, links: prior.links ?? item.links, started_at: prior.started_at ?? item.started_at, last_activity_at: prior.last_activity_at ?? item.last_activity_at, progress: prior.progress ?? item.progress, plan: prior.plan ?? item.plan, phase: prior.phase ?? item.phase, stage: prior.stage ?? item.stage, worktree: prior.worktree ?? item.worktree, timeline: [...(prior.timeline ?? []), ...(item.timeline ?? [])].filter((event, index, events) => events.findIndex(candidate => candidate.at === event.at && candidate.state === event.state) === index), wiring: prior.wiring ?? item.wiring } })
    } else if (!current) found.set(key, { key, item })
  }
  for (const item of snapshot.waiting_on_ove ?? []) found.set(`wait:${item.id}`, { key: `wait:${item.id}`, item })
  return [...found.values()].sort((a, b) => (a.item.seq ?? Number.MAX_SAFE_INTEGER) - (b.item.seq ?? Number.MAX_SAFE_INTEGER) || a.key.localeCompare(b.key))
}
export function selectedTask(snapshot: Snapshot, key: string | null): Task | WaitingItem | FinishedItem | null {
  if (!key) return null
  return taskSelection(snapshot).find(row => row.key === key)?.item ?? null
}
export function rawTaskId(key: string): string { return key.startsWith('task:') || key.startsWith('wait:') ? key.slice(key.indexOf(':') + 1) : key }
export type BucketRow = TaskSelection | { key: string; item: PrRow }
// A bridge task in waiting_on_ove stays in `active` on the server but is
// waiting on Ove, not working: it belongs to the Waiting bucket.
export function isAwaitingOve(item: Task): boolean { return item.state.toLowerCase() === 'waiting_on_ove' }
export function workingTasks(snapshot: Snapshot): Task[] { return (snapshot.active ?? []).filter(item => !isAwaitingOve(item) && !['done', 'failed', 'complete', 'finished'].includes(item.state.toLowerCase())) }
export function waitingRows(snapshot: Snapshot): Array<{ key: string; item: Task | WaitingItem }> {
  const asks = snapshot.waiting_on_ove ?? []
  const askIds = new Set(asks.map(item => item.id))
  const rows: Array<{ key: string; item: Task | WaitingItem }> = asks.map(item => ({ key: `wait:${item.id}`, item }))
  for (const item of snapshot.active ?? []) if (isAwaitingOve(item) && !askIds.has(item.id)) rows.push({ key: `task:${item.id}`, item })
  return rows
}
export function idleTasks(snapshot: Snapshot): Task[] { return snapshot.idle_sessions ?? [] }
export function bucketRows(snapshot: Snapshot, bucket: 'all' | 'waiting' | 'working' | 'pr-gate' | 'finished'): BucketRow[] {
  if (bucket === 'waiting') return waitingRows(snapshot)
  if (bucket === 'working') return workingTasks(snapshot).map(item => ({ key: `task:${item.id}`, item }))
  if (bucket === 'pr-gate') return (snapshot.pr_gate ?? []).map(item => ({ key: `pr:${item.id}`, item }))
  if (bucket === 'finished') {
    const seen = new Set<string>()
    return (snapshot.finished ?? []).filter(item => { if (seen.has(item.id)) return false; seen.add(item.id); return true }).map(item => ({ key: `task:${item.id}`, item }))
  }
  const idleIds = new Set((snapshot.idle_sessions ?? []).map(item => item.id))
  return taskSelection(snapshot).filter(({ key }) => !key.startsWith('task:') || !idleIds.has(key.slice(5)))
}
function samePullRequestUrl(first: string, second: string): boolean {
  try {
    const a = new URL(first), b = new URL(second)
    return a.protocol === 'https:' && b.protocol === 'https:' && a.hostname === 'github.com' && b.hostname === 'github.com' && a.pathname.replace(/\/+$/, '') === b.pathname.replace(/\/+$/, '')
  } catch { return false }
}
export function taskPrRows(snapshot: Snapshot, task: Task | WaitingItem | FinishedItem): CiRow[] {
  const rows = snapshot.sources?.github?.command_center?.ci_cd ?? []
  const links = 'links' in task ? task.links ?? [] : []
  const explicit = rows.filter(row => row.kind === 'pull_request' && row.url && links.some(link => samePullRequestUrl(link.path_or_url, row.url!)))
  if (explicit.length) return explicit
  const wiring = 'wiring' in task ? task.wiring : null
  if (!wiring?.branch) return []
  // Branch names repeat across repositories: without a known repository a
  // branch match is not evidence, so only an explicit link can claim a PR.
  if (!wiring.repo) return []
  const matches = rows.filter(row => row.kind === 'pull_request' && row.branch === wiring.branch && row.repo?.toLowerCase() === wiring.repo!.toLowerCase())
  return matches.length === 1 ? matches : []
}
export function tasksForPr(snapshot: Snapshot, pr: PrRow): TaskSelection[] {
  const source = (snapshot.sources?.github?.command_center?.ci_cd ?? []).find(row => row.kind === 'pull_request' && row.number === pr.number && row.repo?.toLowerCase() === pr.repo.toLowerCase())
  return taskSelection(snapshot).filter(({ item }) => {
    const links = 'links' in item ? item.links ?? [] : []
    const linked = Boolean(source?.url && links.some(link => samePullRequestUrl(link.path_or_url, source.url!)))
    if (linked) return true
    const wiring = 'wiring' in item ? item.wiring : null
    return Boolean(wiring?.branch && wiring.branch === pr.branch && wiring.repo?.toLowerCase() === pr.repo.toLowerCase())
  })
}
export function observedActivity(snapshot: Snapshot): Array<{ at: string; label: string; id: string }> {
  const rows: Array<{ at: string; label: string; id: string }> = []
  for (const selection of taskSelection(snapshot)) for (const event of selection.item.timeline ?? []) rows.push({ at: event.at, label: event.state, id: selection.key })
  for (const source of Object.values(snapshot.sources ?? {})) for (const value of source.items ?? []) {
    if (value && typeof value === 'object' && 'completed_at' in value) { const record = value as { completed_at?: unknown; name?: unknown; id?: unknown }; if (typeof record.completed_at === 'string') rows.push({ at: record.completed_at, label: `Check completed: ${String(record.name ?? 'check')}`, id: String(record.id ?? 'check') }) }
  }
  const commandCenter = snapshot.sources?.github?.command_center
  for (const merge of commandCenter?.recent_merges ?? []) if (merge.merged_at) rows.push({ at: merge.merged_at, label: `Merged ${merge.repo ?? 'repository'} #${merge.number ?? '?'}`, id: merge.id })
  for (const row of commandCenter?.ci_cd ?? []) for (const check of row.checks ?? []) if (check.completed_at) rows.push({ at: check.completed_at, label: `Check completed: ${check.name}`, id: check.id ?? `${row.id}:${check.name}` })
  return rows.filter(row => Number.isFinite(Date.parse(row.at))).sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
}
export function correlateWiring(row: CiRow, tasks: Array<Task | FinishedItem>): Array<Task | FinishedItem> {
  if (!row.branch || !row.head_sha) return []
  const matches = tasks.filter(task => {
    const wiring = task.wiring
    if (!wiring || wiring.branch !== row.branch || !wiring.head_sha) return false
    if (row.repo && (!wiring.repo || wiring.repo.toLowerCase() !== row.repo.toLowerCase())) return false
    if (!row.repo && wiring.repo) return false
    return row.head_sha!.startsWith(wiring.head_sha) || wiring.head_sha.startsWith(row.head_sha!)
  })
  return matches.length === 1 ? matches : []
}
export function sortRepositories(rows: Repository[], query: string, sort: 'name' | 'pushed' | 'visibility'): Repository[] {
  const needle = query.trim().toLocaleLowerCase()
  return rows.filter(row => `${row.full_name} ${row.name} ${row.language ?? ''}`.toLocaleLowerCase().includes(needle)).slice().sort((a, b) => {
    const first = sort === 'name' ? a.full_name.localeCompare(b.full_name) : sort === 'visibility' ? (a.visibility ?? '').localeCompare(b.visibility ?? '') : (b.pushed_at ?? '').localeCompare(a.pushed_at ?? '')
    return first || String(a.id).localeCompare(String(b.id))
  })
}
export function githubFreshness(snapshot: Snapshot): { updatedAt: string | null; stale: boolean; partial: boolean } {
  const meta = snapshot.sources?.github?.command_center?.meta
  const updatedAt = meta?.fetched_at ?? null
  const age = updatedAt ? Date.now() - Date.parse(updatedAt) : 0
  return { updatedAt, stale: Boolean(meta?.stale || (Number.isFinite(age) && age > 120_000)), partial: Boolean(meta?.partial || snapshot.sources?.github?.status === 'error' || snapshot.sources?.github?.status === 'degraded') }
}
