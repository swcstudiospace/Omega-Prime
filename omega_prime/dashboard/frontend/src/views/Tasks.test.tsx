import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Tasks } from './Tasks'
import { fixture } from '../test/fixtures'
describe('Tasks', () => {
  it('filters by title without changing canonical numbering and selects detail inline', () => { render(<Tasks snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} selected={null} onSelect={vi.fn()} />); fireEvent.change(screen.getByLabelText('Search work'), { target: { value: 'Approve' } }); expect(screen.getByText('00008 - Approve deployment')).toBeInTheDocument(); expect(screen.queryByText('00001')).not.toBeInTheDocument() })
})
