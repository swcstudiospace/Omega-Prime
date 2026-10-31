import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Overview } from './Overview'
import { fixture } from '../test/fixtures'
describe('Overview Idle sessions', () => {
  it('excludes idle sessions from Active and renders the separate group', () => {
    render(<Overview snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)
    expect(screen.getByRole('link', { name: 'View Working tasks, 1' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Idle sessions (1)' }))
    expect(screen.getByRole('link', { name: 'Quiet sandbox' })).toHaveAttribute('href', '#/tasks/idle%2F2')
  })
})
