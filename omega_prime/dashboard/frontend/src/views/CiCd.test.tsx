import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CiCd } from './CiCd'
import { fixture } from '../test/fixtures'
describe('CI/CD', () => {
  it('shows PR checks, default-branch evidence and labels unsupported wiring unknown', () => { render(<CiCd snapshot={fixture} data={fixture} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />); expect(screen.getByText('build')).toBeInTheDocument(); expect(screen.getAllByText(/Unknown · no unique branch\/SHA corroboration/)).toHaveLength(1); expect(screen.getByRole('heading', { name: 'main' })).toBeInTheDocument() })
})
