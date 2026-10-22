const SERVED_EXACT = new Set(['/', '/cc', '/legacy', '/healthz'])
const SERVED_PREFIXES = ['/cc/', '/static/', '/api/']
function isServedRoute(path: string): boolean {
  const bare = path.split(/[?#]/)[0]!
  return SERVED_EXACT.has(bare) || SERVED_PREFIXES.some(prefix => bare.startsWith(prefix))
}
export function safeHref(value: unknown, origin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const raw = value.trim()
  if (raw.startsWith('//') || raw.startsWith('\\\\')) return null
  // Filesystem paths (a worktree's phase.log, ~/notes) are not served over
  // HTTP: keep them as text instead of a same-origin link to a missing page.
  if (raw.startsWith('/') && !isServedRoute(raw)) return null
  if (raw.startsWith('~') || raw.startsWith('.') || /^[A-Za-z]:[\\/]/.test(raw)) return null
  try {
    const parsed = new URL(raw, origin)
    if (parsed.protocol !== 'https:' && parsed.origin !== origin) return null
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
    return parsed.href
  } catch { return null }
}
