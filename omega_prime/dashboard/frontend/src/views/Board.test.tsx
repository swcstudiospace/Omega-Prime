import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { Board } from './Board'
import { boardColumns } from '../lib/board'
import { fixture } from '../test/fixtures'
import type { Snapshot } from '../types'

afterEach(cleanup)
const snapshot: Snapshot = { ...fixture, active: [{ ...fixture.active![0]!, title: '00039 - Compile release' }, { ...fixture.active![0]!, id: 'proc:1', title: 'omp process 1', state: 'blocked', detail: null, display_id: '00063 - omp process 1', wiring: null, links: [] }] }
const view = (onTask = vi.fn()) => render(<Board snapshot={snapshot} data={snapshot} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} onTask={onTask} />)

describe('Kanban board', () => {
  it('groups rows into bucket columns with Blocked split from Working', () => {
    expect(boardColumns(snapshot).map(column => [column.key, column.items.length])).toEqual([['working', 1], ['blocked', 1], ['waiting', 1], ['pr-gate', 1], ['finished', 1]])
  })
  it('renders id, title and description and links each card into task detail', () => {
    const onTask = vi.fn()
    view(onTask)
    const working = screen.getByRole('region', { name: 'Working' })
    const card = within(working).getByRole('link', { name: '00039 Compile release' })
    expect(card).toHaveAttribute('href', '#/tasks/worker%2F1')
    expect(within(working).getByText('Compile and verify the release.')).toBeInTheDocument()
    fireEvent.click(card)
    expect(onTask).toHaveBeenCalledWith('worker/1')
    // The PR card links through to its uniquely associated task.
    expect(within(screen.getByRole('region', { name: 'PR gate' })).getByRole('link', { name: 'Fix safe view' })).toHaveAttribute('href', '#/tasks/worker%2F1')
  })
  it('shows malformed rows as not tracked instead of hiding them, with an opt-out toggle', () => {
    view()
    const blocked = screen.getByRole('region', { name: 'Blocked' })
    expect(within(blocked).getByRole('note')).toHaveTextContent('Malformed — not tracked')
    expect(within(blocked).getByRole('note')).toHaveTextContent('no 000NN - prefix · missing description')
    expect(within(blocked).getByText('No description recorded')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /Show malformed rows/ }))
    expect(within(blocked).queryByRole('note')).not.toBeInTheDocument()
  })
})
