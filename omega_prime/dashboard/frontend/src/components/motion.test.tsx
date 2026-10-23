import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { NumberTicker } from './NumberTicker'
import { AnimatedShinyText } from './AnimatedShinyText'
import { BorderBeam } from './BorderBeam'
vi.mock('motion/react', async () => { const actual = await vi.importActual<typeof import('motion/react')>('motion/react'); return { ...actual, useReducedMotion: () => true } })
describe('motion primitives', () => {
  it('renders final values and text immediately with reduced motion', () => { render(<><NumberTicker value={12} /><AnimatedShinyText>Ready</AnimatedShinyText><BorderBeam /></>); expect(screen.getByText('12')).toBeInTheDocument(); expect(screen.getByText('Ready')).not.toHaveClass('shiny-text'); expect(document.querySelector('.border-beam')).toBeNull() })
})
