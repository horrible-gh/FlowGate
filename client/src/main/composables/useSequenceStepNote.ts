export interface SequenceStatusItem {
  status?: string | null
}

/** Index of the first unfinished row; all-done sequences return items.length. */
export function findSequenceHeadIndex(items: readonly SequenceStatusItem[]): number {
  const index = items.findIndex(item => item.status !== 'done')
  return index === -1 ? items.length : index
}