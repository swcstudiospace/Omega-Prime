import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Tasks } from './Tasks'
import { fixture } from '../test/fixtures'
describe('Tasks', () => {
  it('filters by title without changing canonical numbering', () => { render(<Tasks snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} bucket="all" notice={null} onTask={vi.fn()} />); fireEvent.change(screen.getByLabelText('Search work'), { target: { value: 'Approve' } }); expect(screen.getByText('Approve deployment')).toBeInTheDocument(); expect(screen.queryByText('00001')).not.toBeInTheDocument() })
})
