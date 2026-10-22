import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TOAST_MS, Toaster } from './Toaster'
import { makeEvent } from '../lib/events'

vi.mock('motion/react', async () => { const actual = await vi.importActual<typeof import('motion/react')>('motion/react'); return { ...actual, useReducedMotion: () => true } })
afterEach(() => { cleanup(); vi.useRealTimers() })
const toast = (severity: 'info' | 'error', taskId: string | null = null) => makeEvent({ type: severity === 'error' ? 'task.blocked' : 'pr.gate_ready', severity, taskId, title: severity === 'error' ? 'Task blocked' : 'PR gate 5/5', summary: '00007 · Compile release', payload: {} }, { at: '2026-01-01T00:00:00Z', source: 'ws' })

describe('Toaster', () => {
  it('announces errors as alerts, links the task and shows Sydney time', () => {
    render(<Toaster toasts={[toast('error', 'worker/1'), toast('info')]} onDismiss={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Task blocked')
    expect(screen.getByRole('status')).toHaveTextContent('PR gate 5/5')
    expect(screen.getByRole('link', { name: 'Open task' })).toHaveAttribute('href', '#/tasks/worker%2F1')
    expect(screen.getByRole('alert')).toHaveTextContent('AEDT')
  })
  it('dismisses on click and auto-dismisses after its severity timeout, pausing on hover', () => {
    vi.useFakeTimers()
    const onDismiss = vi.fn()
    const item = toast('info')
    render(<Toaster toasts={[item]} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss PR gate 5/5' }))
    expect(onDismiss).toHaveBeenCalledWith(item.id)
    onDismiss.mockClear()
    fireEvent.mouseEnter(screen.getByRole('status'))
    act(() => { vi.advanceTimersByTime(TOAST_MS.info + 1000) })
    expect(onDismiss).not.toHaveBeenCalled()
    fireEvent.mouseLeave(screen.getByRole('status'))
    act(() => { vi.advanceTimersByTime(TOAST_MS.info) })
    expect(onDismiss).toHaveBeenCalledWith(item.id)
  })
  it('keeps a keyboard-focused toast open after the pointer leaves', () => {
    vi.useFakeTimers()
    const onDismiss = vi.fn()
    render(<Toaster toasts={[toast('info', 'worker/1')]} onDismiss={onDismiss} />)
    const status = screen.getByRole('status')
    fireEvent.mouseEnter(status)
    fireEvent.focus(screen.getByRole('link', { name: 'Open task' }))
    fireEvent.mouseLeave(status)
    act(() => { vi.advanceTimersByTime(TOAST_MS.info * 3) })
    expect(onDismiss).not.toHaveBeenCalled()
    // Focus moving between controls inside the toast still holds it.
    fireEvent.blur(screen.getByRole('link', { name: 'Open task' }), { relatedTarget: screen.getByRole('button', { name: 'Dismiss PR gate 5/5' }) })
    act(() => { vi.advanceTimersByTime(TOAST_MS.info * 3) })
    expect(onDismiss).not.toHaveBeenCalled()
    fireEvent.blur(screen.getByRole('button', { name: 'Dismiss PR gate 5/5' }), { relatedTarget: document.body })
    act(() => { vi.advanceTimersByTime(TOAST_MS.info) })
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
