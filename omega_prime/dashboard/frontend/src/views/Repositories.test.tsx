import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Repositories } from './Repositories'
import { fixture } from '../test/fixtures'
describe('Repositories', () => {
  it('searches available inventory and preserves safe GitHub links', () => { render(<Repositories snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />); expect(screen.getByText('swcstudiospace/console')).toBeInTheDocument(); fireEvent.change(screen.getByLabelText('Search repositories'), { target: { value: 'absent' } }); expect(screen.getByText('No repositories match this search.')).toBeInTheDocument() })
})
