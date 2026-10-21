import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { formatSydney } from '../lib/time'
import './TerminalPanel.css'

type Status = 'live' | 'no_session' | 'pane_closed' | 'unavailable'
type Frame = { status: Status; text: string; updated_at: string | null }
const empty: Frame = { status: 'live', text: '', updated_at: null }
const RECONNECT_MS = 30_000
const KEY_NAMES: Record<string, string> = {
  '\r': 'enter', '\t': 'tab', '\x1b[A': 'up', '\x1b[B': 'down',
  '\x1b[C': 'right', '\x1b[D': 'left', '\x1b': 'escape', '\x7f': 'backspace',
}

export function TerminalPanel({ taskId, note }: { taskId: string; note?: string | null }) {
  return <TerminalPanelStream key={taskId} taskId={taskId} note={note} />
}

function TerminalSurface({ onData, terminalRef }: { onData: (payload: string) => void; terminalRef: RefObject<Terminal | null> }) {
  const surface = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const terminal = new Terminal({ convertEol: true, rows: 24, cols: 80 })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminalRef.current = terminal
    const element = surface.current
    // Fit the local terminal to its container on mount and whenever the panel
    // resizes, so narrow task panels and mobile layouts never clip output.
    const refit = () => { try { fit.fit() } catch { /* not laid out yet */ } }
    let observer: ResizeObserver | undefined
    if (element) {
      terminal.open(element)
      refit()
      if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(refit); observer.observe(element) }
    }
    window.addEventListener('resize', refit)
    const subscription = terminal.onData(onData)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', refit)
      subscription.dispose()
      terminal.dispose()
      if (terminalRef.current === terminal) terminalRef.current = null
    }
  }, [onData, terminalRef])
  return <div className="terminal-screen" role="region" aria-label="Interactive terminal" tabIndex={0} ref={surface} />
}

function TerminalPanelStream({ taskId, note }: { taskId: string; note?: string | null }) {
  const [session, setSession] = useState<{ readonly: boolean } | null>(null)
  const [sessionFailed, setSessionFailed] = useState(false)
  const [attached, setAttached] = useState(true)
  const [roToggle, setRoToggle] = useState(false)
  const [wsOpen, setWsOpen] = useState(false)
  const [wsFailed, setWsFailed] = useState(false)
  const [epoch, setEpoch] = useState(0)
  const [paneStatus, setPaneStatus] = useState<Status | null>(null)
  const [frame, setFrame] = useState<Frame>(empty)
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const [copyStatus, setCopyStatus] = useState('')
  const tokenRef = useRef<string | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const sourceRef = useRef<EventSource | null>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const writableRef = useRef(false)
  const noRetryRef = useRef(false)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const region = useRef<HTMLDivElement>(null)

  const interactive = session !== null && typeof WebSocket !== 'undefined' && attached && !wsFailed
  const degraded = sessionFailed || wsFailed || (session !== null && typeof WebSocket === 'undefined')
  const effectiveReadOnly = roToggle || (session?.readonly ?? false)
  useEffect(() => { writableRef.current = wsOpen && !effectiveReadOnly }, [wsOpen, effectiveReadOnly])

  const handleData = useCallback((payload: string) => {
    const socket = socketRef.current
    if (socket === null || !writableRef.current) return
    const named = KEY_NAMES[payload]
    socket.send(JSON.stringify(named ? { type: 'keys', keys: [named] } : { type: 'input', text: payload }))
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch('/api/session').then(async response => {
      if (!response.ok) throw new Error(`Session request failed (${response.status})`)
      const body = await response.json() as { token?: unknown; readonly?: unknown }
      if (typeof body.token !== 'string' || typeof body.readonly !== 'boolean') throw new Error('Malformed session payload')
      if (cancelled) return
      tokenRef.current = body.token
      setSession({ readonly: body.readonly })
    }).catch(() => { if (!cancelled) setSessionFailed(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!interactive || session === null) return
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const next = new WebSocket(`${protocol}://${window.location.host}/ws/tasks/${encodeURIComponent(taskId)}/terminal`)
      socketRef.current = next
      next.onopen = () => {
        next.send(JSON.stringify({ type: 'auth', token: tokenRef.current ?? '', mode: effectiveReadOnly ? 'ro' : 'rw' }))
        setWsOpen(true)
      }
      next.onmessage = (event: MessageEvent<string>) => {
        try {
          const message = JSON.parse(event.data) as { type?: string; text?: unknown; updated_at?: unknown; status?: string }
          if (message.type === 'frame') {
            const text = typeof message.text === 'string' ? message.text : ''
            terminalRef.current?.reset()
            terminalRef.current?.write(text)
            setPaneStatus(null)
            setFrame(previous => ({ status: 'live', text, updated_at: typeof message.updated_at === 'string' ? message.updated_at : previous.updated_at }))
          } else if (message.type === 'status') {
            if (message.status === 'pane_closed') { noRetryRef.current = true; setPaneStatus('pane_closed'); next.close() }
            else if (message.status === 'no_session') setPaneStatus('no_session')
            else if (message.status === 'unavailable') setPaneStatus('unavailable')
            else if (message.status === 'live') setPaneStatus(null)
          }
        } catch { /* malformed server frame: ignore */ }
      }
      next.onclose = () => {
        if (socketRef.current === next) socketRef.current = null
        setWsOpen(false)
        if (noRetryRef.current || !attached) return
        setWsFailed(true)
        if (retryTimerRef.current === undefined) retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = undefined
          noRetryRef.current = false
          setWsFailed(false)
          setEpoch(value => value + 1)
        }, RECONNECT_MS)
      }
      next.onerror = () => { /* the close event always follows */ }
    }
    connect()
    return () => {
      const socket = socketRef.current
      if (socket !== null) { socketRef.current = null; socket.onclose = null; socket.close() }
      setWsOpen(false)
    }
    // effectiveReadOnly is a dependency on purpose: the server fixes the mode
    // at authentication, so a toggle must reconnect and authenticate again.
  }, [interactive, session, taskId, attached, epoch, effectiveReadOnly])

  useEffect(() => () => { clearTimeout(retryTimerRef.current) }, [])

  const connectSse = useCallback(() => {
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
    if (!degraded) return
    const source = connectSse()
    return () => { source.close(); if (sourceRef.current === source) sourceRef.current = null }
  }, [degraded, connectSse])

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
  const toggleAttach = () => {
    if (attached) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = undefined
      setAttached(false)
      return
    }
    noRetryRef.current = false
    setPaneStatus(null)
    setWsFailed(false)
    setAttached(true)
    setEpoch(value => value + 1)
  }

  if (degraded) {
    const status = frame.status === 'no_session' ? 'No session attached' : frame.status === 'pane_closed' ? 'Pane closed' : frame.status === 'unavailable' ? 'Herdr unavailable' : connected ? frame.text ? 'Live' : 'Live · no output yet' : frame.text ? 'Reconnecting…' : 'Connecting to terminal…'
    return <section className="terminal-panel" aria-labelledby="terminal-heading">
      <div className="terminal-toolbar"><div><h3 id="terminal-heading">Live terminal</h3><p>Read only · redacted · last 200 lines</p><p role="status">{status}{paused ? ' · Auto-scroll paused' : ''}</p></div>
        <div className="terminal-actions"><button type="button" aria-pressed={paused} onClick={() => setPaused(value => !value)}>{paused ? 'Resume auto-scroll' : 'Pause auto-scroll'}</button><button type="button" onClick={copy} disabled={!frame.text}>Copy output</button></div></div>
      <p className="terminal-updated">{frame.updated_at ? `Last updated ${formatSydney(frame.updated_at)}` : 'Last updated: not yet received'}</p>
      <div className="terminal-output" role="region" aria-label="Read-only terminal output" tabIndex={0} ref={region} onScroll={onScroll}><pre>{frame.text}</pre></div>
      {frame.status === 'no_session' && <p>This task has no mapped Herdr pane. Other task details remain available.{note ? ` Last note: ${note}` : ''}</p>}
      {frame.status === 'pane_closed' && <p>This session has ended. Other task details remain available.</p>}
      {!connected && frame.text && frame.status === 'live' && <p>Showing the last received output while the connection is restored.</p>}
      {frame.status === 'unavailable' && <p>Terminal output cannot be read on this host. Other task details remain available.</p>}
      <p role="status">{copyStatus}</p>
    </section>
  }

  const badge = paneStatus === 'pane_closed' ? 'Pane closed' : paneStatus === 'unavailable' ? 'Herdr unavailable' : paneStatus === 'no_session' ? 'No session attached' : !attached ? 'Detached' : wsOpen ? (effectiveReadOnly ? 'Read-only' : 'Attached') : 'Live'
  return <section className="terminal-panel" aria-labelledby="terminal-heading">
    <div className="terminal-toolbar"><div><h3 id="terminal-heading">Live terminal</h3><p>Interactive · redacted · Herdr pane mirror</p><p role="status" className="terminal-badge">{badge}</p></div>
      <div className="terminal-actions"><button type="button" onClick={toggleAttach}>{attached ? 'Detach' : 'Attach'}</button><button type="button" aria-pressed={roToggle} onClick={() => setRoToggle(value => !value)}>Read-only</button><button type="button" onClick={copy} disabled={!frame.text}>Copy output</button></div></div>
    <p className="terminal-updated">{frame.updated_at ? `Last updated ${formatSydney(frame.updated_at)}` : 'Last updated: not yet received'}</p>
    {session !== null
      ? <TerminalSurface onData={handleData} terminalRef={terminalRef} />
      : <div className="terminal-screen" role="region" aria-label="Interactive terminal" tabIndex={0} />}
    {paneStatus === 'no_session' && note ? <p>No session attached — last note: {note}</p> : null}
    {paneStatus === 'unavailable' && <p>Terminal output cannot be read on this host. Other task details remain available.</p>}
    {paneStatus === 'pane_closed' && <p>This session has ended. Other task details remain available.</p>}
    <p role="status">{copyStatus}</p>
  </section>
}
