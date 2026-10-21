import type { CiRow } from '../types'
export function reviewLabel(row: CiRow): string {
  const review = row.greptile_review
  if (row.kind !== 'pull_request') return 'No PR review for this commit'
  if (!review || typeof review.score !== 'number') return 'Greptile review unavailable'
  const pull = row.number ? ` · from #${row.number}` : ''
  const sha = review.reviewed_sha ? '' : ' · reviewed SHA not reported'
  return `Greptile ${review.score}/5${pull}${sha}`
}
