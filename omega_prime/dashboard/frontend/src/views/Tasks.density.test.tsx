import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Tasks } from './Tasks'
import { fixture } from '../test/fixtures'
describe('Task row density', () => {
  it('toggles compact field rich rows without losing bucket controls', () => {
    render(<Tasks snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="working" notice={null} onTask={vi.fn()} />)
    expect(screen.getByRole('group', { name: 'Row density' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compact' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Greptile 4/5 · from #12')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Comfortable' }))
    expect(screen.getByRole('button', { name: 'Comfortable' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('link', { name: 'Working, 1 task. Selected; activate to show All.' })).toHaveAttribute('href', '#/tasks')
  })
  it('keeps bucket and density controls available for an empty list', () => {
    render(<Tasks snapshot={{}} data={{}} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="finished" notice={null} onTask={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'No finished tasks yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'All' })).toHaveAttribute('href', '#/tasks')
    expect(screen.getByRole('button', { name: 'Comfortable' })).toBeInTheDocument()
  })
})
