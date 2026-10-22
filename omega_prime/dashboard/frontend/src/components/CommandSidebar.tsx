import { bucketRows, idleTasks, isWaiting, rawTaskId, taskSelection } from '../lib/selectors'
import { bucketHref, taskHref, type TaskBucket } from '../lib/routes'
import type { Snapshot } from '../types'
import type { View } from '../App'
const views: Array<{ key: View; label: string; mark: string }> = [{ key: 'overview', label: 'Overview', mark: '◈' }, { key: 'tasks', label: 'Tasks', mark: '▤' }, { key: 'cicd', label: 'CI / CD', mark: '⌘' }, { key: 'repositories', label: 'Repositories', mark: '⌂' }]
const bucketLinks: Array<{ key: Exclude<TaskBucket, 'all'>; label: string }> = [{ key: 'working', label: 'Working' }, { key: 'waiting', label: 'Waiting on Ove' }, { key: 'pr-gate', label: 'PR gate' }, { key: 'finished', label: 'Finished' }]
type Props = { snapshot: Snapshot; view: View; bucket: TaskBucket; taskId: string | null; onTask: (id: string, bucket: TaskBucket, kind?: 'task' | 'wait') => void }
function bucketCount(snapshot: Snapshot, bucket: Exclude<TaskBucket, 'all'>): string {
  const count = bucketRows(snapshot, bucket).length
  return `${count} ${bucket === 'pr-gate' ? count === 1 ? 'PR' : 'PRs' : count === 1 ? 'task' : 'tasks'}`
}
export function CommandSidebar({ snapshot, view, bucket, taskId, onTask }: Props) {
  const idle = idleTasks(snapshot)
  const idleIds = new Set(idle.map(item => item.id))
  const rows = taskSelection(snapshot).filter(({ key }) => !key.startsWith('task:') || !idleIds.has(key.slice(5))).map(({ key, item }) => ({ key, kind: key.startsWith('wait:') ? 'wait' as const : 'task' as const, id: rawTaskId(key), label: item.display_id ?? 'ID unavailable', title: isWaiting(item) ? item.ask : item.title }))
  return <aside className="sidebar" aria-label="Command navigation">
    <a href="#main-content" className="skip-link">Skip to content</a>
    <div className="brand"><span className="brand-mark">Ω</span><span>OMEGA <b>PRIME</b><small>COMMAND CENTER</small></span></div>
    <nav aria-label="Main navigation">{views.map(item => <a key={item.key} href={item.key === 'tasks' ? '#/tasks' : `#/${item.key}`} className={`nav-link ${view === item.key ? 'active' : ''}`} aria-current={view === item.key ? 'page' : undefined}><span aria-hidden="true">{item.mark}</span>{item.label}</a>)}</nav>
    <section className="sidebar-tasks">
      <nav className="sidebar-buckets" aria-label="Task buckets">
        {bucketLinks.map(item => {
          const selected = view === 'tasks' && bucket === item.key
          return <a key={item.key} href={bucketHref(selected ? 'all' : item.key)} aria-current={selected ? 'page' : undefined} aria-label={selected ? `${item.label}, ${bucketCount(snapshot, item.key)}. Selected; activate to show All.` : item.label}>{item.label}</a>
        })}
        <a href="#/tasks" aria-current={view === 'tasks' && bucket === 'all' ? 'page' : undefined}>All</a>
      </nav>
      <h2>WORK QUEUE <span>{rows.length}</span></h2>
      {rows.length ? rows.map(row => <a key={row.key} title={row.title} className={`queue-link ${taskId === row.id ? 'selected' : ''}`} href={taskHref(row.id)} onClick={() => onTask(row.id, bucket, row.kind)}><span>{row.label}</span><span className="truncate">{row.title}</span></a>) : <p className="subtle">No task records yet.</p>}
      <details className="sidebar-idle"><summary>Idle sessions ({idle.length})</summary>{idle.length ? idle.map(item => <a key={item.id} href={taskHref(item.id)} onClick={() => onTask(item.id, bucket)}>{item.display_id ?? 'ID unavailable'} · {item.title}</a>) : <p>No idle sessions.</p>}</details>
    </section>
    <footer className="sidebar-footer"><span className="status-pip" /> Local-first · Read only</footer>
  </aside>
}
