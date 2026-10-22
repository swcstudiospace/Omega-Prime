import type { CiRow, CommandCenter, FinishedItem, PrRow, Snapshot, Source, Task, WaitingItem } from '../types'
import { samePullRequestUrl, taskSelection, tasksForPr } from './selectors'

/** Where an event was observed: a websocket delta/snapshot, the SSE
 *  fallback stream, an HTTP snapshot fetch (poll/repair/refresh), the task
 *  terminal, or the client. */
export type EventOrigin = 'ws' | 'sse' | 'poll' | 'terminal' | 'client'
export type EventSeverity = 'info' | 'success' | 'warning' | 'error'
export type EventCategory = 'task' | 'pr' | 'terminal' | 'system'
export type EventType =
  | 'snapshot.baseline' | 'stream.collapsed'
  | 'task.added' | 'task.state' | 'task.blocked' | 'task.finished' | 'task.stale' | 'task.recovered' | 'task.removed'
  | 'waiting.added' | 'waiting.cleared'
  | 'pr.opened' | 'pr.closed' | 'pr.ci' | 'pr.greptile' | 'pr.gate_ready' | 'pr.merged'
  | 'source.degraded' | 'source.recovered'
  | 'connection.lost' | 'connection.restored' | 'dashboard.error'
  | 'terminal.attached' | 'terminal.detached' | 'terminal.status' | 'terminal.closed' | 'terminal.auth_retry' | 'terminal.frame' | 'terminal.fallback'

export type Payload = Record<string, string | number | boolean | null>
export type DashEvent = {
  id: string
  /** ISO-8601 instant the client observed the event. */
  at: string
  type: EventType
  source: EventOrigin
  severity: EventSeverity
  /** Task identity (snapshot row id) when the event belongs to one task. */
  taskId: string | null
  title: string
  summary: string
  payload: Payload
}
export type EventDraft = Omit<DashEvent, 'id' | 'at' | 'source'> & { source?: EventOrigin; at?: string }

export const EVENT_LOG_CAP = 500
const TERMINAL_STATES = new Set(['done', 'failed', 'complete', 'finished'])

let counter = 0
/** Monotonic, collision-free id for one client session. */
export function nextEventId(): string { counter += 1; return `evt-${Date.now().toString(36)}-${counter.toString(36)}` }

export function categoryOf(type: EventType): EventCategory {
  if (type.startsWith('task.') || type.startsWith('waiting.')) return 'task'
  if (type.startsWith('pr.')) return 'pr'
  if (type.startsWith('terminal.')) return 'terminal'
  return 'system'
}

export function makeEvent(draft: EventDraft, defaults: { at: string; source: EventOrigin }): DashEvent {
  return { ...draft, id: nextEventId(), at: draft.at ?? defaults.at, source: draft.source ?? defaults.source }
}

/** Newest first, bounded: the log never grows past `cap`. */
export function appendEvents(log: DashEvent[], events: DashEvent[], cap = EVENT_LOG_CAP): DashEvent[] {
  if (events.length === 0) return log
  return [...[...events].reverse(), ...log].slice(0, cap)
}

const byId = <T extends { id: string }>(rows: T[] | null | undefined): Map<string, T> => {
  const map = new Map<string, T>()
  for (const row of rows ?? []) if (row && typeof row.id === 'string') map.set(row.id, row)
  return map
}

/** A Greptile score parsed from "5/5", 5 or a CiRow review; null when absent. */
export function greptileScore(value: string | number | null | undefined): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string') return null
  const match = /^\s*(\d+(?:\.\d+)?)\s*\/\s*5\s*$/.exec(value)
  return match ? Number(match[1]) : null
}

const ciPassing = (ci: string | null | undefined) => ['pass', 'success', 'passed', 'green'].includes((ci ?? '').toLowerCase())
const ciFailing = (ci: string | null | undefined) => ['fail', 'failure', 'failed', 'error', 'red', 'cancelled', 'timed_out'].includes((ci ?? '').toLowerCase())

/** The GitHub CI/CD row describing this gate PR, if the collector reported one. */
export function ciRowForPr(snapshot: Snapshot, pr: Pick<PrRow, 'repo' | 'number'>): CiRow | null {
  const repo = pr.repo.toLowerCase()
  return (snapshot.sources?.github?.command_center?.ci_cd ?? []).find(row => row.kind === 'pull_request' && row.number === pr.number && row.repo?.toLowerCase() === repo) ?? null
}

/** True only when the collector confirmed the stored Greptile review covers
 *  the PR's current head. Scores read from PR bodies or issue comments carry
 *  no reviewed SHA, so they can never prove the newest push was reviewed. */
export function reviewCoversHead(snapshot: Snapshot, pr: Pick<PrRow, 'repo' | 'number'>): boolean {
  return ciRowForPr(snapshot, pr)?.greptile_review?.matches_head === true
}

/** The PR gate at 5/5: Greptile 5/5 confirmed on the current head, CI green
 *  and not known to be unmergeable. Drafts still reach the gate but stay held. */
export function gateReady(pr: PrRow, snapshot: Snapshot): boolean {
  return greptileScore(pr.greptile) === 5 && reviewCoversHead(snapshot, pr) && ciPassing(pr.ci) && pr.mergeable !== false
}
/** Eligible under the standing merge posture: at the gate and not a draft. */
export function mergeEligible(pr: PrRow, snapshot: Snapshot): boolean { return gateReady(pr, snapshot) && !pr.draft }

const label = (item: { display_id?: string | null; id: string }, title: string) => `${shortId(item)} · ${title}`
const prLabel = (pr: Pick<PrRow, 'number' | 'repo'>) => `${pr.repo.split('/').pop() ?? pr.repo} #${pr.number}`

/** Leading task number from the canonical "NNNNN - title" display id. */
export function shortId(item: { display_id?: string | null; seq?: number | string | null }): string {
  const match = /^\s*(\d+)/.exec(item.display_id ?? '')
  if (match) return match[1]!
  const seq = typeof item.seq === 'string' ? Number(item.seq) : item.seq
  if (typeof seq === 'number' && Number.isFinite(seq)) return String(seq).padStart(5, '0')
  return '—'
}

/** Every live task identity: active rows plus quiet Herdr panes the backend
 *  parks in `idle_sessions`, so a pause or resume is a state change, never a
 *  removal followed by a fresh add. Active wins when an id appears in both. */
function liveTasks(snapshot: Snapshot): Map<string, Task> {
  const map = byId<Task>(snapshot.idle_sessions)
  for (const [id, row] of byId<Task>(snapshot.active)) map.set(id, row)
  return map
}

/** The newest completion per id: the backend keeps older and newer runs that
 *  share an id, so the latest `finished_at` (else the later row) wins. */
function latestFinished(rows: FinishedItem[] | null | undefined): Map<string, FinishedItem> {
  const map = new Map<string, FinishedItem>()
  for (const row of rows ?? []) {
    if (!row || typeof row.id !== 'string') continue
    const seen = map.get(row.id)
    if (!seen || (Date.parse(row.finished_at ?? '') || 0) >= (Date.parse(seen.finished_at ?? '') || 0)) map.set(row.id, row)
  }
  return map
}
const completionKey = (row: FinishedItem) => JSON.stringify([row.finished_at ?? null, row.result ?? null, row.state ?? null, row.receipt ?? null])

function blockedEvent(row: Task, from: string | null): EventDraft {
  return { type: 'task.blocked', severity: 'error', taskId: row.id, title: 'Task blocked', summary: from ? `${label(row, row.title)} · ${from} → ${row.state}` : `${label(row, row.title)} · ${row.state}`, payload: { from, to: row.state, detail: truncate(row.detail ?? null, 160) } }
}

function taskEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = liveTasks(prev), after = liveTasks(next)
  const finishedBefore = latestFinished(prev.finished), finishedAfter = latestFinished(next.finished)
  for (const [id, row] of finishedAfter) {
    const old = finishedBefore.get(id)
    if (old && completionKey(old) === completionKey(row)) continue
    const failed = (row.state ?? row.result ?? '').toString().toLowerCase().includes('fail')
    out.push({ type: 'task.finished', severity: failed ? 'error' : 'success', taskId: id, title: failed ? 'Task failed' : 'Task finished', summary: label(row, row.title), payload: { result: row.result ?? null, state: row.state ?? null, receipt: row.receipt ?? null } })
  }
  for (const [id, row] of after) {
    const old = before.get(id)
    if (!old) {
      out.push({ type: 'task.added', severity: 'info', taskId: id, title: 'Task started', summary: label(row, row.title), payload: { state: row.state, source: row.source, pane: row.herdr_pane?.pane_id ?? null } })
      // A task discovered already blocked is as actionable as one that just blocked.
      if (row.state === 'blocked') out.push(blockedEvent(row, null))
      continue
    }
    if (old.state !== row.state) {
      if (row.state === 'blocked') out.push(blockedEvent(row, old.state))
      else {
        const finishedState = TERMINAL_STATES.has(row.state.toLowerCase())
        out.push({ type: 'task.state', severity: finishedState ? 'success' : row.state === 'waiting_on_ove' ? 'warning' : 'info', taskId: id, title: 'Task state changed', summary: `${label(row, row.title)} · ${old.state} → ${row.state}`, payload: { from: old.state, to: row.state, detail: truncate(row.detail ?? null, 160) } })
      }
    }
    const wasStale = Boolean(old.stale), isStale = Boolean(row.stale)
    if (!wasStale && isStale) out.push({ type: 'task.stale', severity: 'warning', taskId: id, title: 'Task went stale', summary: `${label(row, row.title)} · ${row.stale!.reason}`, payload: { reason: row.stale!.reason, since: row.stale!.since } })
    if (wasStale && !isStale) out.push({ type: 'task.recovered', severity: 'success', taskId: id, title: 'Task heartbeat recovered', summary: label(row, row.title), payload: { previous_reason: old.stale!.reason } })
  }
  for (const [id, row] of before) {
    if (after.has(id) || finishedAfter.has(id)) continue
    out.push({ type: 'task.removed', severity: 'info', taskId: id, title: 'Task left the active list', summary: label(row, row.title), payload: { last_state: row.state } })
  }
  const waitBefore = byId<WaitingItem>(prev.waiting_on_ove), waitAfter = byId<WaitingItem>(next.waiting_on_ove)
  for (const [id, item] of waitAfter) if (!waitBefore.has(id)) out.push({ type: 'waiting.added', severity: 'warning', taskId: id, title: 'Waiting on Ove', summary: label(item, item.ask), payload: { blocks: item.blocks ?? null, related_task: item.related_task_id ?? null } })
  for (const [id, item] of waitBefore) if (!waitAfter.has(id)) out.push({ type: 'waiting.cleared', severity: 'success', taskId: id, title: 'Request cleared', summary: label(item, item.ask), payload: { related_task: item.related_task_id ?? null } })
  return out
}

/** The one task a PR belongs to (by recorded link or branch wiring), else null. */
function prTask(snapshot: Snapshot, pr: PrRow): string | null {
  const matches = tasksForPr(snapshot, pr)
  return matches.length === 1 ? matches[0]!.item.id : null
}

type Merge = NonNullable<CommandCenter['recent_merges']>[number]

/** The one task a merge belongs to. The PR may already have left the gate
 *  (the open list refreshed before the merge list did), so fall back to a
 *  task whose recorded links name the merged PR, then to branch wiring from
 *  either snapshot's CI/CD row. */
function mergeTask(prev: Snapshot, next: Snapshot, merge: Merge): string | null {
  const repo = (merge.repo ?? '').toLowerCase()
  const gateRow = [...(prev.pr_gate ?? []), ...(next.pr_gate ?? [])].find(pr => pr.number === merge.number && pr.repo.toLowerCase() === repo)
  if (gateRow) {
    const owner = prTask(prev, gateRow) ?? prTask(next, gateRow)
    if (owner) return owner
  }
  const url = merge.url ?? (merge.repo && merge.number ? `https://github.com/${merge.repo}/pull/${merge.number}` : null)
  if (url) {
    const linked = new Set(taskSelection(next).filter(({ item }) => ('links' in item ? item.links ?? [] : []).some(link => samePullRequestUrl(link.path_or_url, url))).map(({ item }) => item.id))
    if (linked.size === 1) return [...linked][0]!
  }
  for (const snapshot of [next, prev]) {
    const row = merge.repo && typeof merge.number === 'number' ? ciRowForPr(snapshot, { repo: merge.repo, number: merge.number }) : null
    if (!row?.branch || !row.repo) continue
    const wired = new Set(taskSelection(snapshot).filter(({ item }) => {
      const wiring = 'wiring' in item ? item.wiring : null
      return Boolean(wiring && wiring.branch === row.branch && wiring.repo?.toLowerCase() === row.repo!.toLowerCase())
    }).map(({ item }) => item.id))
    if (wired.size === 1) return [...wired][0]!
  }
  return null
}

function prEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = byId<PrRow>(prev.pr_gate), after = byId<PrRow>(next.pr_gate)
  const mergesBefore = new Set((prev.sources?.github?.command_center?.recent_merges ?? []).map(m => String(m.id)))
  const merges = next.sources?.github?.command_center?.recent_merges ?? []
  const mergedNumbers = new Set<string>()
  // Merges are only reported against a baseline from a successful GitHub
  // load: an empty list from a failed first fetch is not a baseline, so a
  // recovery cannot replay historical merges as news.
  if (mergeBaseline(prev)) for (const merge of merges) {
    if (mergesBefore.has(String(merge.id))) continue
    mergedNumbers.add(`${(merge.repo ?? '').toLowerCase()}#${merge.number ?? ''}`)
    out.push({ type: 'pr.merged', severity: 'success', taskId: mergeTask(prev, next, merge), title: 'PR merged', summary: `${(merge.repo ?? 'repo').split('/').pop()} #${merge.number ?? '?'} · ${merge.title ?? ''}`.trim(), payload: { repo: merge.repo ?? null, number: merge.number ?? null, sha: merge.merge_commit_sha ? merge.merge_commit_sha.slice(0, 7) : null } })
  }
  for (const [id, pr] of after) {
    const old = before.get(id)
    if (!old) {
      out.push({ type: 'pr.opened', severity: 'info', taskId: prTask(next, pr), title: 'PR entered the gate', summary: `${prLabel(pr)} · ${pr.title}`, payload: prPayload(pr) })
      // A PR discovered with CI already red is as actionable as one that just failed.
      if (ciFailing(pr.ci)) out.push({ type: 'pr.ci', severity: 'error', taskId: prTask(next, pr), title: 'CI failed', summary: `${prLabel(pr)} · ${pr.ci}`, payload: prPayload(pr) })
      if (gateReady(pr, next)) out.push(gateEvent(pr, prTask(next, pr)))
      continue
    }
    if (old.ci !== pr.ci) out.push({ type: 'pr.ci', severity: ciFailing(pr.ci) ? 'error' : ciPassing(pr.ci) ? 'success' : 'info', taskId: prTask(next, pr), title: ciFailing(pr.ci) ? 'CI failed' : ciPassing(pr.ci) ? 'CI green' : 'CI changed', summary: `${prLabel(pr)} · ${old.ci} → ${pr.ci}`, payload: prPayload(pr) })
    if (old.greptile !== pr.greptile) out.push({ type: 'pr.greptile', severity: greptileScore(pr.greptile) === 5 ? 'success' : 'info', taskId: prTask(next, pr), title: 'Greptile review', summary: `${prLabel(pr)} · ${old.greptile} → ${pr.greptile}`, payload: prPayload(pr) })
    if (!gateReady(old, prev) && gateReady(pr, next)) out.push(gateEvent(pr, prTask(next, pr)))
  }
  for (const [id, pr] of before) {
    if (after.has(id) || mergedNumbers.has(`${pr.repo.toLowerCase()}#${pr.number}`)) continue
    out.push({ type: 'pr.closed', severity: 'info', taskId: prTask(prev, pr), title: 'PR left the gate', summary: `${prLabel(pr)} · ${pr.title}`, payload: prPayload(pr) })
  }
  return out
}

function prPayload(pr: PrRow): Payload {
  return { repo: pr.repo, number: pr.number, ci: pr.ci, greptile: pr.greptile, draft: pr.draft, mergeable: pr.mergeable, rule: pr.merge_rule }
}
function gateEvent(pr: PrRow, taskId: string | null): EventDraft {
  return { type: 'pr.gate_ready', severity: 'success', taskId, title: 'PR gate 5/5', summary: `${prLabel(pr)} · Greptile 5/5 · CI green · ${pr.draft ? 'draft, held' : pr.merge_rule}`, payload: prPayload(pr) }
}

const HEALTHY = new Set(['ok', 'success', 'empty', 'disabled'])
/** Collector statuses meaning the source's rows could not be read at all. */
const FAILED = new Set(['error', 'unreadable', 'unavailable', 'failed'])
/** A source whose rows are missing because it failed, not because they ended. */
export function sourceFailed(source: Source | null | undefined): boolean {
  return Boolean(source && (FAILED.has(source.status ?? '') || (!HEALTHY.has(source.status ?? '') && source.error)))
}
const mergeBaseline = (snapshot: Snapshot): boolean => {
  const github = snapshot.sources?.github
  return Boolean(github && Array.isArray(github.command_center?.recent_merges) && !sourceFailed(github))
}

function sourceEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = prev.sources ?? {}, after = next.sources ?? {}
  for (const [name, source] of Object.entries(after)) {
    const old = before[name]
    if (!old) continue
    const was = HEALTHY.has(old.status ?? ''), now = HEALTHY.has(source.status ?? '')
    if (was && !now) out.push({ type: 'source.degraded', severity: sourceFailed(source) ? 'error' : 'warning', taskId: null, title: `Source ${source.status ?? 'degraded'}`, summary: `${name.replaceAll('_', ' ')}${source.error ? ` · ${truncate(source.error, 120)}` : ''}`, payload: { source: name, from: old.status ?? null, to: source.status ?? null } })
    if (!was && now) out.push({ type: 'source.recovered', severity: 'success', taskId: null, title: 'Source recovered', summary: name.replaceAll('_', ' '), payload: { source: name, from: old.status ?? null, to: source.status ?? null } })
  }
  return out
}

/** Rows a failed source stopped reporting are unknown, not gone. Carry the
 *  last good rows forward for each failed source so the diff (and the next
 *  baseline) sees no removals during the outage and no replayed additions on
 *  recovery. Source health itself always comes from `next`. */
export function stabilizeSnapshot(prev: Snapshot | null, next: Snapshot): Snapshot {
  if (prev === null) return next
  let out = next
  if (sourceFailed(next.sources?.tasks_json)) out = { ...out, active: prev.active, idle_sessions: prev.idle_sessions, waiting_on_ove: prev.waiting_on_ove, finished: prev.finished }
  const github = next.sources?.github
  if (github && sourceFailed(github)) {
    const merges = prev.sources?.github?.command_center?.recent_merges
    out = { ...out, pr_gate: prev.pr_gate, sources: { ...out.sources, github: { ...github, command_center: { ...github.command_center, recent_merges: merges ?? github.command_center?.recent_merges ?? null } } } }
  }
  return out
}

/** Diff two dashboard snapshots into structured events. Pure: the caller
 *  stamps time and transport. A null `prev` yields a single baseline event,
 *  so the first snapshot never replays existing state as news. */
export function deriveEvents(prev: Snapshot | null, next: Snapshot): EventDraft[] {
  if (prev === null) {
    const active = next.active?.length ?? 0, waiting = next.waiting_on_ove?.length ?? 0, prs = next.pr_gate?.length ?? 0
    return [{ type: 'snapshot.baseline', severity: 'info', taskId: null, title: 'Snapshot received', summary: `${active} active · ${waiting} waiting on Ove · ${prs} PRs at the gate`, payload: { active, waiting, prs, generated_at: next.generated_at ?? null } }]
  }
  if (prev === next) return []
  return [...taskEvents(prev, next), ...prEvents(prev, next), ...sourceEvents(prev, next)]
}

/** Toast-worthy events: the ones Ove acts on or should notice. */
const TOAST_TYPES = new Set<EventType>(['task.state', 'task.blocked', 'task.finished', 'task.stale', 'waiting.added', 'pr.gate_ready', 'pr.merged', 'connection.lost', 'connection.restored', 'dashboard.error', 'source.degraded'])
export function shouldToast(event: DashEvent): boolean {
  if (event.type === 'pr.ci') return event.severity === 'error'
  if (event.type === 'source.degraded') return event.severity === 'error'
  return TOAST_TYPES.has(event.type)
}

export function truncate(value: string | null | undefined, max: number): string | null {
  if (value === null || value === undefined) return null
  const flat = value.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

/** One-line `key=value` rendering of a payload for log rows. */
export function payloadSummary(payload: Payload, max = 140): string {
  const parts = Object.entries(payload).filter(([, value]) => value !== null && value !== '').map(([key, value]) => `${key}=${String(value)}`)
  return truncate(parts.join(' · '), max) ?? ''
}

/** The event belongs to this task: its own identity, or a waiting request
 *  linked to it (added or cleared) through `related_task`. */
export function matchesTask(event: DashEvent, taskId: string): boolean {
  return event.taskId === taskId || ((event.type === 'waiting.added' || event.type === 'waiting.cleared') && event.payload.related_task === taskId)
}

export type EventFilter = 'all' | EventCategory | 'alerts'
export function filterEvents(events: DashEvent[], filter: EventFilter, query = ''): DashEvent[] {
  const needle = query.trim().toLocaleLowerCase()
  return events.filter(event => {
    if (filter === 'alerts' ? !(event.severity === 'error' || event.severity === 'warning') : filter !== 'all' && categoryOf(event.type) !== filter) return false
    if (!needle) return true
    return `${event.type} ${event.title} ${event.summary} ${payloadSummary(event.payload, 1000)}`.toLocaleLowerCase().includes(needle)
  })
}

