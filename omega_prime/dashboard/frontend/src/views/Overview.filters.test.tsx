import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { Overview, overviewRows } from './Overview'
import { fixture } from '../test/fixtures'
import type { Snapshot } from '../types'
import { EventLogContext, EMPTY_EVENT_LOG } from '../lib/eventContext'
import { makeEvent } from '../lib/events'

afterEach(cleanup)
const snapshot: Snapshot = { ...fixture, active: [fixture.active![0]!, { ...fixture.active![0]!, id: 'gb:00039', title: '00039 - Sandbox stack', state: 'blocked', display_id: '00374 - 00039 - Sandbox stack', detail: '14:05 digest: #67 pushed' }] }
const view = (events = EMPTY_EVENT_LOG.events) => render(<EventLogContext.Provider value={{ ...EMPTY_EVENT_LOG, events }}><Overview snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} /></EventLogContext.Provider>)

describe('Overview landing page', () => {
  it('filters current work by clickable status chips', () => {
    expect(overviewRows(snapshot, 'blocked').map(row => row.id)).toEqual(['gb:00039'])
    expect(overviewRows(snapshot, 'malformed').map(row => row.id)).toEqual(['worker/1', 'wait-1', 'idle/2'])
    view()
    const list = () => within(document.querySelector('.overview-task-list') as HTMLElement)
    expect(list().getAllByRole('listitem')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Blocked (1)' }))
    expect(list().getAllByRole('listitem')).toHaveLength(1)
    expect(list().getByRole('link', { name: 'Sandbox stack' })).toHaveAttribute('href', '#/tasks/gb%3A00039')
    expect(list().getByText('14:05 digest: #67 pushed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Blocked (1)' }))
    expect(list().getAllByRole('listitem')).toHaveLength(3)
  })
  it('keeps ids in a fixed column and flags malformed rows', () => {
    view()
    const row = screen.getByRole('link', { name: 'Compile release' }).closest('li')!
    expect(row).toHaveClass('is-malformed')
    expect(within(row).getByRole('note')).toHaveTextContent('Malformed — not tracked')
    expect(within(row).getByText('00007')).toHaveClass('card-id')
  })
  it('shows the live event stream and a blocked summary card', () => {
    view([makeEvent({ type: 'task.blocked', severity: 'error', taskId: 'gb:00039', title: 'Task blocked', summary: '00374 · Sandbox stack', payload: {} }, { at: '2026-01-01T00:00:00Z', source: 'ws' })])
    expect(screen.getByRole('link', { name: 'View Blocked tasks, 1' })).toHaveAttribute('href', '#/board')
    expect(within(screen.getByRole('list', { name: 'Event stream entries' })).getByText('task.blocked')).toBeInTheDocument()
  })
})
