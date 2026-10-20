export function safeHref(value: unknown, origin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const raw = value.trim()
  if (raw.startsWith('//') || raw.startsWith('\\\\')) return null
  try {
    const parsed = new URL(raw, origin)
    if (parsed.protocol !== 'https:' && parsed.origin !== origin) return null
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
    return parsed.href
  } catch { return null }
}
