export type TaskBucket = 'all' | 'waiting' | 'working' | 'pr-gate' | 'finished'
export type View = 'overview' | 'tasks' | 'cicd' | 'repositories'
export type RouteState = { view: View; bucket: TaskBucket; taskId: string | null; notice: string | null }
const buckets: Record<Exclude<TaskBucket, 'all'>, true> = { waiting: true, working: true, 'pr-gate': true, finished: true }
function isBucket(value: string): value is Exclude<TaskBucket, 'all'> { return Object.prototype.hasOwnProperty.call(buckets, value) }
export function parseHash(hash: string): RouteState {
  const path = hash.replace(/^#/, '')
  if (path === '/overview' || path === '' || path === '/') return { view: 'overview', bucket: 'all', taskId: null, notice: null }
  if (path === '/tasks' || path.startsWith('/tasks?')) {
    const params = new URLSearchParams(path.split('?')[1] ?? '')
    const raw = params.get('bucket')
    return { view: 'tasks', bucket: raw && isBucket(raw) ? raw : 'all', taskId: null, notice: raw && !isBucket(raw) ? 'Unknown bucket. Showing All tasks.' : null }
  }
  if (path.startsWith('/tasks/')) {
    try { return { view: 'tasks', bucket: 'all', taskId: decodeURIComponent(path.slice('/tasks/'.length)), notice: null } }
    catch { return { view: 'tasks', bucket: 'all', taskId: path.slice('/tasks/'.length), notice: null } }
  }
  if (path === '/cicd') return { view: 'cicd', bucket: 'all', taskId: null, notice: null }
  if (path === '/repositories') return { view: 'repositories', bucket: 'all', taskId: null, notice: null }
  return { view: 'overview', bucket: 'all', taskId: null, notice: null }
}
export function taskHref(id: string): string { return `#/tasks/${encodeURIComponent(id)}` }
export function bucketHref(bucket: TaskBucket): string { return bucket === 'all' ? '#/tasks' : `#/tasks?bucket=${bucket}` }
export function taskOrigin(id: string, state: RouteState): TaskBucket {
  const historyState = window.history.state as { taskOrigin?: unknown; taskOriginTaskId?: unknown } | null
  if (historyState?.taskOriginTaskId === id && typeof historyState.taskOrigin === 'string' && (historyState.taskOrigin === 'all' || isBucket(historyState.taskOrigin))) return historyState.taskOrigin as TaskBucket
  return state.bucket
}
