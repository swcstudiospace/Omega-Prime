/** Shared by the board badges and the event stream, kept free of either
 *  module's imports so both can use it without an import cycle. */
const flatText = (value: string | null | undefined): string | null => {
  const flat = (value ?? '').replace(/\s+/g, ' ').trim()
  return flat ? flat : null
}

/** Board/badge state for a finished row: the recorded state, else its result
 *  (`ok`-style codes read as `done`), keeping failed and stopped distinct. */
export function finishedState(item: { state?: string | null; result?: string | null }): string {
  const state = flatText(item.state)
  if (state) return state
  const result = flatText(item.result)
  if (!result) return 'done'
  const code = result.toLowerCase()
  if (['ok', 'done', 'success', 'succeeded', 'complete', 'completed', 'finished'].includes(code)) return 'done'
  if (['fail', 'failed', 'failure', 'error', 'timeout', 'timed_out'].includes(code)) return 'failed'
  if (['stopped', 'cancelled', 'canceled'].includes(code)) return 'stopped'
  return result
}
