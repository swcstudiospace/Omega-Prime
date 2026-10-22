export function formatSydney(value?: string | null): string {
  if (!value) return 'Unavailable'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return 'Unavailable'
  return new Intl.DateTimeFormat('en-AU', { timeZone: 'Australia/Sydney', year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(date)
}
export function ageLabel(value?: string | null, now = Date.now()): string {
  if (!value) return 'Age unavailable'
  const time = Date.parse(value)
  if (!Number.isFinite(time)) return 'Age unavailable'
  const seconds = Math.max(0, Math.floor((now - time) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}
/** Wall-clock time in Sydney with seconds and the zone label, for log rows. */
export function formatSydneyClock(value?: string | null): string {
  if (!value) return 'Unavailable'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return 'Unavailable'
  return new Intl.DateTimeFormat('en-AU', { timeZone: 'Australia/Sydney', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZoneName: 'short' }).format(date)
}
