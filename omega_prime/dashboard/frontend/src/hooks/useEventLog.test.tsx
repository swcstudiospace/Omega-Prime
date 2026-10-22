import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { MAX_TOASTS, useEventLog } from './useEventLog'
import { fixture } from '../test/fixtures'
import type { Snapshot } from '../types'
import type { EventOrigin } from '../lib/events'

type Props = { data: Snapshot | null; origin?: EventOrigin | null; live: boolean; error: string | null }
// Mirrors useDashboard: a live socket delivers over ws, otherwise HTTP polling,
// unless a test names the transport explicitly.
const setup = (initial: Props) => renderHook((props: Props) => useEventLog(props.data, props.origin === undefined ? (props.data ? (props.live ? 'ws' : 'poll') : null) : props.origin, props.live, props.error), { initialProps: initial })
const task = fixture.active![0]!

describe('useEventLog', () => {
  it('logs a baseline without toasting, then logs and toasts real changes', () => {
    const { result, rerender } = setup({ data: null, live: false, error: null })
    rerender({ data: fixture, live: true, error: null })
    expect(result.current.events.map(event => event.type)).toEqual(['snapshot.baseline'])
    expect(result.current.toasts).toHaveLength(0)
    rerender({ data: { ...fixture, active: [{ ...task, state: 'blocked' }] }, live: true, error: null })
    expect(result.current.events[0]).toMatchObject({ type: 'task.blocked', source: 'ws' })
    expect(result.current.toasts.map(toast => toast.type)).toEqual(['task.blocked'])
    act(() => result.current.dismissToast(result.current.toasts[0]!.id))
    expect(result.current.toasts).toHaveLength(0)
  })
  it('marks events from polling and collapses a burst into a summary toast', () => {
    const { result, rerender } = setup({ data: fixture, live: false, error: null })
    const many = Array.from({ length: 6 }, (_, i) => ({ ...task, id: `w/${i}`, title: `T${i}` }))
    rerender({ data: { ...fixture, active: many.map(row => ({ ...row, state: 'running' })) }, live: false, error: null })
    rerender({ data: { ...fixture, active: many.map(row => ({ ...row, state: 'blocked' })) }, live: false, error: null })
    expect(result.current.events[0]!.source).toBe('poll')
    expect(result.current.toasts.length).toBeLessThanOrEqual(MAX_TOASTS)
    expect(result.current.toasts.at(-1)).toMatchObject({ type: 'stream.collapsed', title: '4 more updates' })
  })
  it('reports connection loss, restoration and errors', () => {
    const { result, rerender } = setup({ data: fixture, live: false, error: null })
    rerender({ data: fixture, live: true, error: null })
    expect(result.current.events.some(event => event.type.startsWith('connection'))).toBe(false)
    rerender({ data: fixture, live: false, error: null })
    rerender({ data: fixture, live: true, error: null })
    rerender({ data: fixture, live: true, error: 'Snapshot request failed (500)' })
    rerender({ data: fixture, live: true, error: 'Snapshot request failed (500)' })
    expect(result.current.events.map(event => event.type).slice(0, 3)).toEqual(['dashboard.error', 'connection.restored', 'connection.lost'])
    expect(result.current.toasts.map(toast => toast.type)).toEqual(['connection.lost', 'connection.restored', 'dashboard.error'])
  })
  it('labels each event with the transport that actually delivered it', () => {
    const { result, rerender } = setup({ data: fixture, origin: 'ws', live: true, error: null })
    // The SSE fallback also reports live; its events must not claim the socket.
    rerender({ data: { ...fixture, active: [{ ...task, state: 'quiet' }] }, origin: 'sse', live: true, error: null })
    expect(result.current.events[0]).toMatchObject({ type: 'task.state', source: 'sse' })
    // An HTTP repair/refresh while live is still an HTTP delivery.
    rerender({ data: { ...fixture, active: [{ ...task, state: 'running' }] }, origin: 'poll', live: true, error: null })
    expect(result.current.events[0]).toMatchObject({ type: 'task.state', source: 'poll' })
  })
  it('keeps last-good rows through a source outage so recovery replays nothing', () => {
    const healthy: Snapshot = { ...fixture, sources: { ...fixture.sources, tasks_json: { status: 'ok' } } }
    const { result, rerender } = setup({ data: healthy, live: true, error: null })
    // The task file's rows vanish; other collectors keep reporting theirs.
    const down: Snapshot = { ...healthy, finished: [], sources: { ...healthy.sources, tasks_json: { status: 'unreadable', error: 'bad json' } } }
    rerender({ data: down, live: true, error: null })
    expect(result.current.events.map(event => event.type)).toEqual(['source.degraded', 'snapshot.baseline'])
    expect(result.current.toasts.map(toast => toast.type)).toEqual(['source.degraded'])
    rerender({ data: { ...healthy }, live: true, error: null })
    expect(result.current.events.map(event => event.type).slice(0, 1)).toEqual(['source.recovered'])
    expect(result.current.events.some(event => ['task.added', 'task.finished', 'waiting.added', 'task.removed', 'waiting.cleared'].includes(event.type))).toBe(false)
  })
  it('records client terminal events and clears the log', () => {
    const { result } = setup({ data: null, live: false, error: null })
    act(() => result.current.log({ type: 'terminal.attached', severity: 'success', taskId: 'worker/1', title: 'Terminal attached', summary: '', payload: {}, source: 'terminal' }))
    expect(result.current.events[0]).toMatchObject({ source: 'terminal', taskId: 'worker/1' })
    act(() => result.current.clear())
    expect(result.current.events).toHaveLength(0)
  })
})
