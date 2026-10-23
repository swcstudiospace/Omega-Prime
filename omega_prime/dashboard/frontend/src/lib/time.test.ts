import { describe, expect, it } from 'vitest'
import { ageLabel, formatSydney } from './time'
describe('Sydney date formatting', () => {
  it('formats valid timestamps in Sydney time and seasonal zones', () => { expect(formatSydney('2026-01-01T00:00:00Z')).toContain('AEDT'); expect(formatSydney('2026-07-01T00:00:00Z')).toContain('AEST') })
  it('does not invent invalid timestamps', () => { expect(formatSydney(null)).toBe('Unavailable'); expect(formatSydney('bad')).toBe('Unavailable'); expect(ageLabel('bad')).toBe('Age unavailable') })
})
