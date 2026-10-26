import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Overview } from './Overview'
import { fixture } from '../test/fixtures'
describe('Overview', () => {
  it('shows real counts and honest absence of observed merge events', () => { const { container } = render(<Overview snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />); expect(container.textContent).toContain('Active work'); expect(screen.getByText('No verified recent merge records available.')).toBeInTheDocument(); expect(screen.getByText('Persisted observations')).toBeInTheDocument() })
})
