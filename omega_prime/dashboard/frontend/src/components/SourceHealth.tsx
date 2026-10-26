import type { Source } from '../types'
export function SourceHealth({ sources = {} }: { sources?: Record<string, Source> }) {
  const entries = Object.entries(sources)
  return <section className="panel"><div className="section-heading"><div><p className="eyebrow">OBSERVABILITY</p><h2>Source health</h2></div><span className="section-count">{entries.length} sources</span></div>{entries.length ? <div className="health-list">{entries.map(([name, source]) => <div className="health-row" key={name}><span className={`health-dot ${source.status === 'ok' || source.status === 'success' ? 'healthy' : 'degraded'}`} /><strong>{name.replaceAll('_', ' ')}</strong><span>{source.status ?? 'unknown'}</span>{source.error && <small>{source.error}</small>}</div>)}</div> : <p className="empty-state">Source status unavailable.</p>}</section>
}
