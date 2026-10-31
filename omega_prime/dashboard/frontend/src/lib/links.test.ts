import { describe, expect, it } from 'vitest'
import { safeHref } from './links'
describe('safeHref', () => {
  it('accepts HTTPS and genuine same-origin links', () => { expect(safeHref('https://github.com/org/repo')).toBe('https://github.com/org/repo'); expect(safeHref('/legacy', 'http://localhost')).toBe('http://localhost/legacy') })
  it('rejects script, data, protocol-relative and local filesystem URLs', () => { for (const value of ['javascript:alert(1)', 'data:text/html,bad', '//evil.test/path', 'file:///etc/passwd']) expect(safeHref(value, 'http://localhost')).toBeNull() })
})
