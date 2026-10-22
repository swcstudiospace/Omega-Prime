import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { EventStream, newerThan } from './EventStream'
import { makeEvent, type EventDraft } from '../lib/events'

afterEach(cleanup)
const draft = (type: EventDraft['type'], severity: EventDraft['severity'], summary: string, taskId: string | null = null): EventDraft => ({ type, severity, taskId, title: type, summary, payload: { state: 'blocked', pane: 'wP:p1' } })
const events = [draft('task.blocked', 'error', 'Compile release blocked', 'worker/1'), draft('pr.gate_ready', 'success', 'console #12 at 5/5'), draft('terminal.attached', 'success', 'pane open', 'worker/1')]
  .map((item, i) => makeEvent(item, { at: `2026-01-01T00:00:0${i}Z`, source: item.type.startsWith('terminal') ? 'terminal' : 'ws' }))

describe('EventStream', () => {
  it('renders structured rows with Sydney time, type, source and payload', () => {
    render(<EventStream events={events} />)
    const rows = within(screen.getByRole('list', { name: 'Event stream entries' })).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('AEDT')
    expect(rows[0]).toHaveTextContent('task.blocked')
    expect(rows[0]).toHaveTextContent('ws')
    expect(rows[0]).toHaveTextContent('state=blocked · pane=wP:p1')
    expect(within(rows[0]!).getByRole('link', { name: 'open' })).toHaveAttribute('href', '#/tasks/worker%2F1')
  })
  it('filters by category, alerts and search text', () => {
    render(<EventStream events={events} />)
    fireEvent.click(screen.getByRole('button', { name: /^Terminal/ }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /^Alerts/ }))
    expect(screen.getByRole('listitem')).toHaveTextContent('task.blocked')
    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    fireEvent.change(screen.getByLabelText('Search events'), { target: { value: '5/5' } })
    expect(screen.getByRole('listitem')).toHaveTextContent('pr.gate_ready')
  })
  it('pauses on a frozen view and reports how many arrived meanwhile', () => {
    const { rerender } = render(<EventStream events={events.slice(1)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    rerender(<EventStream events={events} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Resume (1 new)' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })
  it('counts arrivals by identity once the bounded log stops growing', () => {
    // At the cap a new event evicts the oldest, so the length is unchanged.
    const { rerender } = render(<EventStream events={events.slice(1)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    rerender(<EventStream events={events.slice(0, 2)} />)
    expect(screen.getByRole('button', { name: 'Resume (1 new)' })).toBeInTheDocument()
    expect(newerThan(events, events[2]!.id)).toBe(2)
    expect(newerThan(events, 'evicted')).toBe(3)
    expect(newerThan(events, null)).toBe(3)
  })
})
