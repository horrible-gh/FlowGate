import type { DiffLine } from './changeDiff'

export type DiffContextRow =
  | { type: 'line'; line: DiffLine }
  | {
      type: 'gap'
      count: number
      oldStart: number | null
      oldEnd: number | null
      newStart: number | null
      newEnd: number | null
    }

/**
 * Keep context around changes, merging touching windows. Gap ranges are inclusive
 * original line numbers. Line objects are preserved; the input is never mutated.
 * No display limit is applied. contextLines must be a non-negative finite integer.
 */
export function projectDiffContext(lines: DiffLine[], contextLines = 3): DiffContextRow[] {
  if (!Number.isInteger(contextLines) || contextLines < 0) {
    throw new RangeError('contextLines must be a non-negative finite integer')
  }
  if (!lines.some((line) => line.kind !== 'context')) return []

  const rows: DiffContextRow[] = []
  let i = 0
  while (i < lines.length) {
    if (lines[i].kind !== 'context') {
      rows.push({ type: 'line', line: lines[i++] })
      continue
    }
    const start = i
    while (i < lines.length && lines[i].kind === 'context') i += 1
    // A context run can have a change on either side (or both).
    const leftEnd = start > 0 ? Math.min(i, start + contextLines) : start
    const rightStart = i < lines.length ? Math.max(start, i - contextLines) : i
    if (leftEnd >= rightStart) {
      for (let j = start; j < i; j++) rows.push({ type: 'line', line: lines[j] })
      continue
    }
    for (let j = start; j < leftEnd; j++) rows.push({ type: 'line', line: lines[j] })
    rows.push({
      type: 'gap',
      count: rightStart - leftEnd,
      oldStart: lines[leftEnd].oldNo,
      oldEnd: lines[rightStart - 1].oldNo,
      newStart: lines[leftEnd].newNo,
      newEnd: lines[rightStart - 1].newNo,
    })
    for (let j = rightStart; j < i; j++) rows.push({ type: 'line', line: lines[j] })
  }
  return rows
}
