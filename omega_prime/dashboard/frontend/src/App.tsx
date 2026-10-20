import { useState } from 'react'
import { useDashboard } from './hooks/useDashboard'
import { CommandSidebar } from './components/CommandSidebar'
import { Overview } from './views/Overview'
import { Tasks } from './views/Tasks'
import { CiCd } from './views/CiCd'
import { Repositories } from './views/Repositories'
import type { Snapshot } from './types'
export type View = 'overview' | 'tasks' | 'cicd' | 'repositories'
export function App() {
  const dashboard = useDashboard()
  const [view, setView] = useState<View>('overview')
  const [selected, setSelected] = useState<string | null>(null)
  const snapshot: Snapshot = dashboard.data ?? {}
  return <div className="app-shell"><CommandSidebar snapshot={snapshot} view={view} onView={setView} selected={selected} onSelect={id => { setSelected(id); setView('tasks') }} />
    <main id="main-content" className="main-content"><header className="topbar"><div><p className="eyebrow">OMEGA PRIME / COMMAND CENTER</p><h1>{({ overview: 'Overview', tasks: 'Task Control', cicd: 'CI / CD', repositories: 'Repositories' })[view]}</h1></div><div className="connection" aria-live="polite"><span className={`connection-dot ${dashboard.live ? 'is-live' : ''}`} />{dashboard.live ? 'Live' : 'Polling'}<button className="quiet-button" onClick={dashboard.refresh} disabled={dashboard.loading}>Refresh</button></div></header>
    {view === 'overview' && <Overview {...dashboard} snapshot={snapshot} />}{view === 'tasks' && <Tasks {...dashboard} snapshot={snapshot} selected={selected} onSelect={setSelected} />}{view === 'cicd' && <CiCd {...dashboard} snapshot={snapshot} />}{view === 'repositories' && <Repositories {...dashboard} snapshot={snapshot} />}</main></div>
}
