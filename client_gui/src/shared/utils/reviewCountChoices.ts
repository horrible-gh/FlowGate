// Review-count choices shared by the card Inspector (AI tab) and the AI group role table, so both offer and keep
// the same values: the server's choices without "until it passes" (-1), and a stored value outside them stays shown.

/** Counts the server may still list but that can no longer be newly picked (-1 = until it passes). */
export const REVIEW_COUNT_HIDDEN: readonly number[] = [-1]

export type ReviewCountKind = 'until_pass' | 'none' | 'times'

/** What a review count means: -1 until it passes, 0 none (안 함), n rounds. */
export function reviewCountKind(count: number): ReviewCountKind {
  if (count === -1) return 'until_pass'
  if (count === 0) return 'none'
  return 'times'
}

/**
 * The options a review-count select offers: every hidden count is dropped unless it is the stored one.
 * `current` is the stored value (number or its text), compared as text.
 */
export function visibleReviewCountOptions<T extends { readonly value: string | number }>(
  options: readonly T[],
  current: string | number | null | undefined,
): T[] {
  const shown = current === null || current === undefined ? null : String(current)
  return options.filter(option => !REVIEW_COUNT_HIDDEN.includes(Number(option.value)) || String(option.value) === shown)
}

/**
 * The review counts to list for a server choice list (numbers, in the server's order) and the stored value:
 * hidden counts are dropped, and a stored value outside the choices is appended so it stays visible.
 */
export function reviewCountValues(choices: readonly number[], stored: number | null | undefined): number[] {
  const values = visibleReviewCountOptions(choices.filter(Number.isInteger).map(value => ({ value })), stored).map(option => option.value)
  if (typeof stored === 'number' && Number.isInteger(stored) && !values.includes(stored)) values.push(stored)
  return values
}
