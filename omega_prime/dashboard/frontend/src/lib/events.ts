import type { PrRow, Snapshot, Task, WaitingItem } from '../types'
import { tasksForPr } from './selectors'

/** Where an event was observed: a websocket delta/snapshot, an HTTP/SSE
 *  refresh while the socket is down, the task terminal, or the client. */
export type EventOrigin = 'ws' | 'poll' | 'terminal' | 'client'
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

/** The PR gate at 5/5: Greptile 5/5 on the reported head, CI green and not
 *  known to be unmergeable. Drafts still reach the gate but stay held. */
export function gateReady(pr: PrRow): boolean {
  return greptileScore(pr.greptile) === 5 && ciPassing(pr.ci) && pr.mergeable !== false
}
/** Eligible under the standing merge posture: at the gate and not a draft. */
export function mergeEligible(pr: PrRow): boolean { return gateReady(pr) && !pr.draft }

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

function taskEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = byId<Task>(prev.active), after = byId<Task>(next.active)
  const finishedBefore = byId(prev.finished), finishedAfter = byId(next.finished)
  for (const [id, row] of finishedAfter) {
    if (finishedBefore.has(id)) continue
    const failed = (row.state ?? row.result ?? '').toString().toLowerCase().includes('fail')
    out.push({ type: 'task.finished', severity: failed ? 'error' : 'success', taskId: id, title: failed ? 'Task failed' : 'Task finished', summary: label(row, row.title), payload: { result: row.result ?? null, state: row.state ?? null, receipt: row.receipt ?? null } })
  }
  for (const [id, row] of after) {
    const old = before.get(id)
    if (!old) {
      out.push({ type: 'task.added', severity: 'info', taskId: id, title: 'Task started', summary: label(row, row.title), payload: { state: row.state, source: row.source, pane: row.herdr_pane?.pane_id ?? null } })
      continue
    }
    if (old.state !== row.state) {
      const blocked = row.state === 'blocked'
      const finishedState = TERMINAL_STATES.has(row.state.toLowerCase())
      out.push({ type: blocked ? 'task.blocked' : 'task.state', severity: blocked ? 'error' : finishedState ? 'success' : row.state === 'waiting_on_ove' ? 'warning' : 'info', taskId: id, title: blocked ? 'Task blocked' : 'Task state changed', summary: `${label(row, row.title)} · ${old.state} → ${row.state}`, payload: { from: old.state, to: row.state, detail: truncate(row.detail ?? null, 160) } })
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

function prEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = byId<PrRow>(prev.pr_gate), after = byId<PrRow>(next.pr_gate)
  const mergesBefore = new Set((prev.sources?.github?.command_center?.recent_merges ?? []).map(m => String(m.id)))
  const merges = next.sources?.github?.command_center?.recent_merges ?? []
  const mergedNumbers = new Set<string>()
  // Merges are only reported once a previous snapshot carried the merge list,
  // so a GitHub source that was empty does not replay history as news.
  if (prev.sources?.github?.command_center?.recent_merges) for (const merge of merges) {
    if (mergesBefore.has(String(merge.id))) continue
    mergedNumbers.add(`${merge.repo ?? ''}#${merge.number ?? ''}`)
    const gateRow = (prev.pr_gate ?? []).find(pr => pr.number === merge.number && pr.repo === merge.repo)
    out.push({ type: 'pr.merged', severity: 'success', taskId: gateRow ? prTask(prev, gateRow) : null, title: 'PR merged', summary: `${(merge.repo ?? 'repo').split('/').pop()} #${merge.number ?? '?'} · ${merge.title ?? ''}`.trim(), payload: { repo: merge.repo ?? null, number: merge.number ?? null, sha: merge.merge_commit_sha ? merge.merge_commit_sha.slice(0, 7) : null } })
  }
  for (const [id, pr] of after) {
    const old = before.get(id)
    if (!old) {
      out.push({ type: 'pr.opened', severity: 'info', taskId: prTask(next, pr), title: 'PR entered the gate', summary: `${prLabel(pr)} · ${pr.title}`, payload: prPayload(pr) })
      if (gateReady(pr)) out.push(gateEvent(pr, prTask(next, pr)))
      continue
    }
    if (old.ci !== pr.ci) out.push({ type: 'pr.ci', severity: ciFailing(pr.ci) ? 'error' : ciPassing(pr.ci) ? 'success' : 'info', taskId: prTask(next, pr), title: ciFailing(pr.ci) ? 'CI failed' : ciPassing(pr.ci) ? 'CI green' : 'CI changed', summary: `${prLabel(pr)} · ${old.ci} → ${pr.ci}`, payload: prPayload(pr) })
    if (old.greptile !== pr.greptile) out.push({ type: 'pr.greptile', severity: greptileScore(pr.greptile) === 5 ? 'success' : 'info', taskId: prTask(next, pr), title: 'Greptile review', summary: `${prLabel(pr)} · ${old.greptile} → ${pr.greptile}`, payload: prPayload(pr) })
    if (!gateReady(old) && gateReady(pr)) out.push(gateEvent(pr, prTask(next, pr)))
  }
  for (const [id, pr] of before) {
    if (after.has(id) || mergedNumbers.has(`${pr.repo}#${pr.number}`)) continue
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
function sourceEvents(prev: Snapshot, next: Snapshot): EventDraft[] {
  const out: EventDraft[] = []
  const before = prev.sources ?? {}, after = next.sources ?? {}
  for (const [name, source] of Object.entries(after)) {
    const old = before[name]
    if (!old) continue
    const was = HEALTHY.has(old.status ?? ''), now = HEALTHY.has(source.status ?? '')
    if (was && !now) out.push({ type: 'source.degraded', severity: source.status === 'error' ? 'error' : 'warning', taskId: null, title: `Source ${source.status ?? 'degraded'}`, summary: `${name.replaceAll('_', ' ')}${source.error ? ` · ${truncate(source.error, 120)}` : ''}`, payload: { source: name, from: old.status ?? null, to: source.status ?? null } })
    if (!was && now) out.push({ type: 'source.recovered', severity: 'success', taskId: null, title: 'Source recovered', summary: name.replaceAll('_', ' '), payload: { source: name, from: old.status ?? null, to: source.status ?? null } })
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

export function matchesTask(event: DashEvent, taskId: string): boolean { return event.taskId === taskId }

export type EventFilter = 'all' | EventCategory | 'alerts'
export function filterEvents(events: DashEvent[], filter: EventFilter, query = ''): DashEvent[] {
  const needle = query.trim().toLocaleLowerCase()
  return events.filter(event => {
    if (filter === 'alerts' ? !(event.severity === 'error' || event.severity === 'warning') : filter !== 'all' && categoryOf(event.type) !== filter) return false
    if (!needle) return true
    return `${event.type} ${event.title} ${event.summary} ${payloadSummary(event.payload, 1000)}`.toLocaleLowerCase().includes(needle)
  })
}

