import type { FinishedItem, PrRow, Snapshot, Task, WaitingItem } from '../types'
import { bucketRows, workingTasks } from './selectors'
import type { TaskBucket } from './routes'

export type BoardColumnKey = 'working' | 'blocked' | 'waiting' | 'pr-gate' | 'finished'
export type BoardColumn =
  | { key: Exclude<BoardColumnKey, 'pr-gate'>; label: string; bucket: TaskBucket | null; items: Array<Task | WaitingItem | FinishedItem> }
  | { key: 'pr-gate'; label: string; bucket: TaskBucket; items: PrRow[] }

/** Group the snapshot into Kanban columns that mirror the task buckets, with
 *  Blocked split out of Working so it is never buried. Pure and read-only. */
export function boardColumns(snapshot: Snapshot): BoardColumn[] {
  const working = workingTasks(snapshot)
  const finished = bucketRows(snapshot, 'finished').map(row => row.item as FinishedItem)
  return [
    { key: 'working', label: 'Working', bucket: 'working', items: working.filter(task => task.state !== 'blocked') },
    { key: 'blocked', label: 'Blocked', bucket: null, items: working.filter(task => task.state === 'blocked') },
    { key: 'waiting', label: 'Waiting on Ove', bucket: 'waiting', items: snapshot.waiting_on_ove ?? [] },
    { key: 'pr-gate', label: 'PR gate', bucket: 'pr-gate', items: snapshot.pr_gate ?? [] },
    { key: 'finished', label: 'Finished', bucket: 'finished', items: finished },
  ]
}
