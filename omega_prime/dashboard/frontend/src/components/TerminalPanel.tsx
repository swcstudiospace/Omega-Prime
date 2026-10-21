import { useCallback, useEffect, useRef, useState } from 'react'
import { formatSydney } from '../lib/time'
import './TerminalPanel.css'

type Status = 'live' | 'no_session' | 'pane_closed' | 'unavailable'
type Frame = { status: Status; text: string; updated_at: string | null }
const empty: Frame = { status: 'live', text: '', updated_at: null }

export function TerminalPanel({ taskId }: { taskId: string }) {
  return <TerminalPanelStream key={taskId} taskId={taskId} />
}

function TerminalPanelStream({ taskId }: { taskId: string }) {
  const [frame, setFrame] = useState<Frame>(empty)
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const [copyStatus, setCopyStatus] = useState('')
  const region = useRef<HTMLDivElement>(null)
  const sourceRef = useRef<EventSource | null>(null)
  const connect = useCallback(() => {
    const source = new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/terminal`)
    sourceRef.current = source
    source.addEventListener('terminal', ((event: MessageEvent<string>) => {
      try {
        const next = JSON.parse(event.data) as Frame
        setFrame(previous => ({ ...next, text: next.text || (next.status === 'unavailable' || next.status === 'pane_closed' ? previous.text : '') }))
        setConnected(next.status === 'live')
        if (next.status === 'no_session' || next.status === 'pane_closed' || next.status === 'unavailable') source.close()
      } catch { setConnected(false) }
    }) as EventListener)
    source.onerror = () => setConnected(false)
    return source
  }, [taskId])
  useEffect(() => {
    setFrame(empty); setConnected(false); setPaused(false); setCopyStatus('')
    const source = connect()
    return () => { source.close(); if (sourceRef.current === source) sourceRef.current = null }
  }, [connect])
  useEffect(() => {
    if (!paused && region.current) region.current.scrollTop = region.current.scrollHeight
  }, [frame.text, paused])
  const copy = async () => {
    try { await navigator.clipboard.writeText(frame.text); setCopyStatus('Output copied') }
    catch { setCopyStatus('Could not copy output. Select the text and copy it manually.') }
  }
  const onScroll = () => {
    const el = region.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight > 8) setPaused(true)
  }
  const status = frame.status === 'no_session' ? 'No session attached' : frame.status === 'pane_closed' ? 'Pane closed' : frame.status === 'unavailable' ? 'Herdr unavailable' : connected ? frame.text ? 'Live' : 'Live · no output yet' : frame.text ? 'Reconnecting…' : 'Connecting to terminal…'
  return <section className="terminal-panel" aria-labelledby="terminal-heading">
    <div className="terminal-toolbar"><div><h3 id="terminal-heading">Live terminal</h3><p>Read only · redacted · last 200 lines</p><p role="status">{status}{paused ? ' · Auto-scroll paused' : ''}</p></div>
      <div className="terminal-actions"><button type="button" aria-pressed={paused} onClick={() => setPaused(value => !value)}>{paused ? 'Resume auto-scroll' : 'Pause auto-scroll'}</button><button type="button" onClick={copy} disabled={!frame.text}>Copy output</button></div></div>
    <p className="terminal-updated">{frame.updated_at ? `Last updated ${formatSydney(frame.updated_at)}` : 'Last updated: not yet received'}</p>
    <div className="terminal-output" role="region" aria-label="Read-only terminal output" tabIndex={0} ref={region} onScroll={onScroll}><pre>{frame.text}</pre></div>
    {frame.status === 'no_session' && <p>This task has no mapped Herdr pane. Other task details remain available.</p>}
    {frame.status === 'pane_closed' && <p>This session has ended. Other task details remain available.</p>}
    {!connected && frame.text && frame.status === 'live' && <p>Showing the last received output while the connection is restored.</p>}
    {frame.status === 'unavailable' && <p>Terminal output cannot be read on this host. Other task details remain available.</p>}
    <p role="status">{copyStatus}</p>
  </section>
}
