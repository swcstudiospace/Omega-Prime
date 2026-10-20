import type { CiRow, FinishedItem, Repository, Snapshot, Task, WaitingItem } from '../types'
export type TaskSelection = { key: string; item: Task | WaitingItem | FinishedItem }
export function taskSelection(snapshot: Snapshot): TaskSelection[] {
  const active = snapshot.active ?? []
  const finished = snapshot.finished ?? []
  const found = new Map<string, TaskSelection>()
  for (const item of active) found.set(`task:${item.id}`, { key: `task:${item.id}`, item })
  for (const item of finished) {
    const key = `task:${item.id}`
    const current = found.get(key)
    if (current && 'state' in current.item) {
      const prior = current.item
      found.set(key, { key, item: { ...prior, detail: prior.detail ?? item.summary ?? item.result, timeline: [...(prior.timeline ?? []), ...(item.timeline ?? [])].filter((event, index, events) => events.findIndex(candidate => candidate.at === event.at && candidate.state === event.state) === index), wiring: prior.wiring ?? item.wiring } })
    } else if (!current) found.set(key, { key, item })
  }
  for (const item of snapshot.waiting_on_ove ?? []) found.set(`wait:${item.id}`, { key: `wait:${item.id}`, item })
  return [...found.values()].sort((a, b) => {
    const aSeq = a.item.seq ?? Number.MAX_SAFE_INTEGER, bSeq = b.item.seq ?? Number.MAX_SAFE_INTEGER
    return aSeq - bSeq || a.key.localeCompare(b.key)
  })
}
export function selectedTask(snapshot: Snapshot, key: string | null): Task | WaitingItem | FinishedItem | null {
  if (!key) return null
  return taskSelection(snapshot).find(row => row.key === key)?.item ?? null
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
