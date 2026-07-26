// flowgate.default.0326 R0001 / NR0005 §5 — row model for the file explorer's
// "변경 내용 보기" view.
//
// The line/token diff engine itself is NOT reimplemented here: `buildChunkSideDiff`
// (useConflictChunks) already computes an LCS line diff with token-level inline
// highlighting, and the merge-conflict resolver has been rendering it since 0182.
// What it returns is two INDEPENDENT side arrays (ours may hold a `removed` line
// that theirs has no row for), which is enough for the conflict dialog's two loose
// columns but not for a file diff that needs an aligned split view AND a unified
// view built from the same data. These helpers turn the two side arrays into one
// aligned row list, and collapse untouched stretches so a 4000-line file with a
// 3-line edit does not render 4000 DOM rows.
import {
  buildChunkSideDiff,
  type DiffLine,
  type DiffLineStatus,
} from './useConflictChunks'

export interface DiffRow {
  status: DiffLineStatus
  // Null on the side that has no line for this row (added / removed).
  left: DiffLine | null
  right: DiffLine | null
  leftNumber: number | null
  rightNumber: number | null
}

export interface DiffRowsResult {
  rows: DiffRow[]
  /**
   * True when the changed region was too large to run the O(n·m) LCS over, so the
   * result degrades to "every old line removed, every new line added". The viewer
   * must say so — a silently approximate diff is worse than no diff.
   */
  approximate: boolean
}

export interface DiffStats {
  added: number
  removed: number
  changed: number
}

export type DiffSection =
  | { kind: 'rows'; rows: DiffRow[] }
  | { kind: 'gap'; count: number }

/**
 * Per-side line budget for the LCS pass. `lcsPairs` allocates an
 * (n+1)×(m+1) number matrix, so an unbounded whole-file diff of a big
 * generated file would exhaust memory in the browser tab. Beyond this the
 * result is flagged `approximate` instead.
 */
export const MAX_LCS_LINES = 1200

/** Default untouched-context lines kept around each change when collapsing. */
export const DEFAULT_CONTEXT_LINES = 3

export function splitTextLines(content: string): string[] {
  if (!content) return []
  const lines = content.split(/\r\n|\n|\r/)
  // A trailing newline yields one empty tail element; it is not a real line.
  return lines.length > 1 && lines[lines.length - 1] === '' ? lines.slice(0, -1) : lines
}

function plainLine(line: string, index: number, status: DiffLineStatus): DiffLine {
  return { line, sourceIndex: index, status, tokens: [{ text: line, status: 'common' }] }
}

function commonRow(line: string, oldNumber: number, newNumber: number): DiffRow {
  return {
    status: 'common',
    left: plainLine(line, oldNumber - 1, 'common'),
    right: plainLine(line, newNumber - 1, 'common'),
    leftNumber: oldNumber,
    rightNumber: newNumber,
  }
}

/**
 * Aligned row list for two versions of one file.
 *
 * Identical head/tail lines are trimmed before the LCS runs — the ordinary shape of
 * a source edit is a few changed lines inside an otherwise identical file, and
 * trimming turns that from an O(file²) matrix into O(edit²).
 */
export function buildDiffRows(oldLines: string[], newLines: string[]): DiffRowsResult {
  const shorter = Math.min(oldLines.length, newLines.length)
  let head = 0
  while (head < shorter && oldLines[head] === newLines[head]) head += 1
  let tail = 0
  while (
    tail < shorter - head
    && oldLines[oldLines.length - 1 - tail] === newLines[newLines.length - 1 - tail]
  ) tail += 1

  const oldMid = oldLines.slice(head, oldLines.length - tail)
  const newMid = newLines.slice(head, newLines.length - tail)
  const approximate = Math.max(oldMid.length, newMid.length) > MAX_LCS_LINES

  const rows: DiffRow[] = []
  for (let i = 0; i < head; i += 1) rows.push(commonRow(oldLines[i], i + 1, i + 1))

  if (approximate) {
    oldMid.forEach((line, i) => rows.push({
      status: 'removed',
      left: plainLine(line, head + i, 'removed'),
      right: null,
      leftNumber: head + i + 1,
      rightNumber: null,
    }))
    newMid.forEach((line, i) => rows.push({
      status: 'added',
      left: null,
      right: plainLine(line, head + i, 'added'),
      leftNumber: null,
      rightNumber: head + i + 1,
    }))
  } else {
    const side = buildChunkSideDiff(oldMid, newMid)
    let i = 0
    let j = 0
    let oldNumber = head + 1
    let newNumber = head + 1
    while (i < side.ours.length || j < side.theirs.length) {
      const left = i < side.ours.length ? side.ours[i] : null
      const right = j < side.theirs.length ? side.theirs[j] : null
      // The two side arrays are produced in lockstep per LCS run (paired
      // changed lines, then ours-only removals, then theirs-only additions,
      // then the common pair), so matching statuses re-pairs them exactly.
      if (left && right && left.status === right.status) {
        rows.push({
          status: left.status,
          left,
          right,
          leftNumber: oldNumber,
          rightNumber: newNumber,
        })
        i += 1
        j += 1
        oldNumber += 1
        newNumber += 1
      } else if (left && left.status !== 'added' && (!right || right.status === 'added' || right.status === 'common')) {
        rows.push({ status: 'removed', left, right: null, leftNumber: oldNumber, rightNumber: null })
        i += 1
        oldNumber += 1
      } else if (right) {
        rows.push({ status: 'added', left: null, right, leftNumber: null, rightNumber: newNumber })
        j += 1
        newNumber += 1
      } else {
        break
      }
    }
  }

  for (let i = 0; i < tail; i += 1) {
    const oldIndex = oldLines.length - tail + i
    const newIndex = newLines.length - tail + i
    rows.push(commonRow(oldLines[oldIndex], oldIndex + 1, newIndex + 1))
  }
  return { rows, approximate }
}

export interface UnifiedRow {
  status: DiffLineStatus
  /** ' ' | '-' | '+' — the gutter marker of a unified patch. */
  sign: string
  line: DiffLine
  leftNumber: number | null
  rightNumber: number | null
}

/**
 * Flatten aligned rows into unified-patch order. A `changed` row becomes the two
 * lines a patch would show (old then new), each keeping its inline tokens.
 */
export function toUnifiedRows(rows: DiffRow[]): UnifiedRow[] {
  const out: UnifiedRow[] = []
  for (const row of rows) {
    if (row.status === 'common' && row.left) {
      out.push({ status: 'common', sign: ' ', line: row.left, leftNumber: row.leftNumber, rightNumber: row.rightNumber })
      continue
    }
    if (row.left) {
      out.push({ status: 'removed', sign: '-', line: row.left, leftNumber: row.leftNumber, rightNumber: null })
    }
    if (row.right) {
      out.push({ status: 'added', sign: '+', line: row.right, leftNumber: null, rightNumber: row.rightNumber })
    }
  }
  return out
}

export function diffStats(rows: DiffRow[]): DiffStats {
  return rows.reduce<DiffStats>((stats, row) => {
    if (row.status === 'added') stats.added += 1
    else if (row.status === 'removed') stats.removed += 1
    else if (row.status === 'changed') stats.changed += 1
    return stats
  }, { added: 0, removed: 0, changed: 0 })
}

/**
 * Split rows into rendered blocks and collapsed gaps, keeping `context` untouched
 * lines on each side of every change. A file with no change at all collapses to a
 * single gap, which the viewer renders as "변경 없음".
 */
export function collapseCommonRows(
  rows: DiffRow[],
  context: number = DEFAULT_CONTEXT_LINES,
): DiffSection[] {
  const keep = rows.map((row) => row.status !== 'common')
  rows.forEach((row, index) => {
    if (row.status === 'common') return
    for (let offset = 1; offset <= context; offset += 1) {
      if (index - offset >= 0) keep[index - offset] = true
      if (index + offset < rows.length) keep[index + offset] = true
    }
  })

  const sections: DiffSection[] = []
  let buffer: DiffRow[] = []
  let gap = 0
  const flushRows = () => {
    if (buffer.length) sections.push({ kind: 'rows', rows: buffer })
    buffer = []
  }
  const flushGap = () => {
    if (gap) sections.push({ kind: 'gap', count: gap })
    gap = 0
  }
  rows.forEach((row, index) => {
    if (keep[index]) {
      flushGap()
      buffer.push(row)
    } else {
      flushRows()
      gap += 1
    }
  })
  flushRows()
  flushGap()
  return sections
}
