import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { App } from './App'
import { fixture } from './test/fixtures'
vi.mock('./hooks/useDashboard', () => ({ useDashboard: () => ({ data: fixture, loading: false, error: null, receivedAt: Date.now(), live: true, refresh: vi.fn() }) }))
describe('Command Center navigation', () => {
  beforeEach(() => { cleanup(); window.history.replaceState(null, ''); window.location.hash = '#/overview'; vi.clearAllMocks() })
  it('renders all views and changes views without document navigation', async () => { const originalPath = window.location.pathname; render(<App />); expect(screen.getByRole('heading', { name: 'Overview', level: 1 })).toBeInTheDocument(); fireEvent.click(screen.getByRole('link', { name: 'Repositories' })); await waitFor(() => expect(screen.getByRole('heading', { name: 'Repositories', level: 1 })).toBeInTheDocument()); expect(window.location.pathname).toBe(originalPath) })
  it('uses canonical display ids in the task sidebar', () => { render(<App />); expect(screen.getAllByRole('link', { name: /00007 - Compile release/ })).toHaveLength(1) })
})
