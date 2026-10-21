import { useEffect, useRef, useState } from 'react'
import { useDashboard } from './hooks/useDashboard'
import { CommandSidebar } from './components/CommandSidebar'
import { Overview } from './views/Overview'
import { Tasks } from './views/Tasks'
import { CiCd } from './views/CiCd'
import { Repositories } from './views/Repositories'
import { TaskDetail } from './components/TaskDetail'
import { parseHash, rememberTaskOrigin, taskHref, taskOrigin, type RouteState, type TaskBucket } from './lib/routes'
import { selectedTask } from './lib/selectors'
import { ViewState } from './components/ViewState'
import type { Snapshot } from './types'
export type View = RouteState['view']
function clearTaskOriginState(): void {
  if (!window.history.state || typeof window.history.state !== 'object') return
  const { taskOrigin: _origin, taskOriginTaskId: _taskId, ...rest } = window.history.state as Record<string, unknown>
  window.history.replaceState(rest, '')
}
export function App() {
  const dashboard = useDashboard()
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  const previousRoute = useRef(route)
  const snapshot: Snapshot = dashboard.data ?? {}
  useEffect(() => {
    if (previousRoute.current.taskId === null) clearTaskOriginState()
    const onChange = () => {
      if (window.location.hash === '#main-content') return
      const next = parseHash(window.location.hash)
      const previous = previousRoute.current
      previousRoute.current = next
      if (previous.taskId !== null && next.taskId === null) clearTaskOriginState()
      setRoute(next)
      requestAnimationFrame(() => {
        const returnLink = previous.taskId !== null && next.taskId === null ? Array.from(document.querySelectorAll<HTMLAnchorElement>('.main-content a')).find(link => link.getAttribute('href') === taskHref(previous.taskId!)) : null
        if (returnLink) returnLink.focus()
        else document.querySelector<HTMLElement>('.main-content h1')?.focus()
      })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  const rememberOrigin = (id: string, bucket: TaskBucket) => {
    rememberTaskOrigin(id, bucket)
    window.history.replaceState({ ...(window.history.state ?? {}), taskOrigin: bucket, taskOriginTaskId: id }, '')
  }
  const resolved = route.taskId === null ? null : selectedTask(snapshot, `task:${route.taskId}`) ?? selectedTask(snapshot, `wait:${route.taskId}`)
  const origin = route.taskId ? taskOrigin(route.taskId, route) : 'all'
  const activeBucket = route.taskId ? origin : route.bucket
  return <div className="app-shell"><CommandSidebar snapshot={snapshot} view={route.view} bucket={activeBucket} taskId={route.taskId} onTask={rememberOrigin} />
    <main id="main-content" className="main-content"><header className="topbar"><div><p className="eyebrow">OMEGA PRIME / COMMAND CENTER</p><h1 tabIndex={-1}>{title}</h1></div><div className="connection" aria-live="polite"><span className={`connection-dot ${dashboard.live ? 'is-live' : ''}`} />{dashboard.live ? 'Live' : 'Polling'}<button className="quiet-button" onClick={dashboard.refresh} disabled={dashboard.loading}>Refresh</button></div></header>
      {route.taskId !== null ? resolved ? <ViewState loading={false} error={dashboard.error} receivedAt={dashboard.receivedAt} retry={dashboard.refresh}><TaskDetail item={resolved} taskId={route.taskId} snapshot={snapshot} backBucket={origin} loading={dashboard.loading} /></ViewState> : !dashboard.data ? dashboard.loading ? <p className="empty-state">Loading task details…</p> : <ViewState loading={false} error={dashboard.error} receivedAt={dashboard.receivedAt} retry={dashboard.refresh}><p className="empty-state">Task details cannot be resolved until snapshot data is available.</p></ViewState> : <section className="panel"><h2>Task not found</h2><p>This task is not in the current snapshot.</p><a href="#/tasks">View tasks</a></section> : route.view === 'overview' ? <Overview {...dashboard} snapshot={snapshot} /> : route.view === 'tasks' ? <Tasks {...dashboard} snapshot={snapshot} bucket={route.bucket} notice={route.notice} onTask={id => rememberOrigin(id, route.bucket)} /> : route.view === 'cicd' ? <CiCd {...dashboard} snapshot={snapshot} /> : <Repositories {...dashboard} snapshot={snapshot} />}</main></div>
}
