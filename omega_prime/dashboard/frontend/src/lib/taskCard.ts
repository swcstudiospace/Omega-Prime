import type { FinishedItem, Task, WaitingItem } from '../types'
import { isWaiting } from './selectors'
import { shortId, truncate } from './events'

/** Ove's tracking rule: every task is "000NN - Title" with a description.
 *  The prefix is matched on its own, so a numbered row with no title text
 *  still reports its number and a missing title rather than a missing prefix. */
const NUMBERED_PREFIX = /^\s*(\d{5})\s+-(?:\s+|$)(.*)$/s
/** Bare outcome codes the producer writes into `result`; they are a status,
 *  not a description of the work. */
const RESULT_CODES = new Set(['ok', 'done', 'success', 'succeeded', 'complete', 'completed', 'finished', 'fail', 'failed', 'failure', 'error', 'stopped', 'cancelled', 'canceled', 'timeout', 'timed_out', 'skipped'])
const isResultCode = (value: string | null): boolean => value !== null && RESULT_CODES.has(value.toLowerCase())

export { finishedState } from './outcome'

export type CardModel = {
  /** Server sequence number from the display id ("00374"). */
  seq: string
  /** Ove's task number from the title ("00039"), null when absent. */
  taskNumber: string | null
  /** Title without the "000NN - " prefix; null when missing. */
  title: string | null
  /** The raw title/ask as recorded, untouched. */
  rawTitle: string
  description: string | null
  /** Why the row breaks the tracking rule; empty when it is well formed. */
  problems: string[]
  malformed: boolean
}

const text = (value: string | null | undefined): string | null => {
  const flat = (value ?? '').replace(/\s+/g, ' ').trim()
  return flat ? flat : null
}

/** Display model for one task row. Read-only: it never rewrites the record,
 *  it only reports what is missing so malformed rows stay visible. */
export function cardModel(item: Task | WaitingItem | FinishedItem): CardModel {
  const seq = shortId(item)
  if (isWaiting(item)) {
    // A request to Ove: its ask is the title, its blocker the description,
    // and the 000NN it belongs to is carried in its id (wait:00037-…).
    const ask = text(item.ask)
    const taskNumber = /(?:^|[:/])(\d{5})(?:\b|-)/.exec(item.id)?.[1] ?? null
    const description = text(item.blocks) ?? (item.related_task_id ? `For ${item.related_task_id}` : null)
    const problems = [...(taskNumber ? [] : ['no 000NN task number']), ...(ask ? [] : ['missing title']), ...(description ? [] : ['missing description'])]
    return { seq, taskNumber, title: ask, rawTitle: item.ask ?? '', description, problems, malformed: problems.length > 0 }
  }
  const raw = item.title ?? ''
  const match = NUMBERED_PREFIX.exec(raw)
  const title = match ? text(match[2]) : text(raw)
  const result = 'result' in item ? text(item.result) : null
  const description = 'result' in item ? text(item.summary) ?? text(item.detail) ?? (isResultCode(result) ? null : result) : text(item.detail)
  const problems = [...(match ? [] : ['no 000NN - prefix']), ...(title ? [] : ['missing title']), ...(description ? [] : ['missing description'])]
  return { seq, taskNumber: match ? match[1]! : null, title, rawTitle: raw, description, problems, malformed: problems.length > 0 }
}

export function snippet(value: string | null, max = 140): string | null { return truncate(value, max) }
