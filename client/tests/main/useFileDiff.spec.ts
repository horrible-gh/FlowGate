// flowgate.default.0326 R0001 — row model behind the file explorer's "변경 내용 보기".
import { describe, expect, it } from 'vitest'

import {
  MAX_LCS_LINES,
  buildDiffRows,
  collapseCommonRows,
  diffStats,
  splitTextLines,
  toUnifiedRows,
} from '@main/composables/useFileDiff'

describe('splitTextLines', () => {
  it('drops only the phantom line a trailing newline produces', () => {
    expect(splitTextLines('a\nb\n')).toEqual(['a', 'b'])
    expect(splitTextLines('a\nb')).toEqual(['a', 'b'])
    expect(splitTextLines('a\n\n')).toEqual(['a', ''])
    expect(splitTextLines('')).toEqual([])
  })

  it('handles CRLF and CR line endings', () => {
    expect(splitTextLines('a\r\nb\r\n')).toEqual(['a', 'b'])
    expect(splitTextLines('a\rb')).toEqual(['a', 'b'])
  })
})

describe('buildDiffRows', () => {
  it('aligns a one-line modification and keeps both line numbers', () => {
    const { rows, approximate } = buildDiffRows(
      ['one', 'two', 'three'],
      ['one', 'TWO', 'three'],
    )
    expect(approximate).toBe(false)
    expect(rows.map((row) => row.status)).toEqual(['common', 'changed', 'common'])
    expect(rows[1].leftNumber).toBe(2)
    expect(rows[1].rightNumber).toBe(2)
    expect(rows[1].left?.line).toBe('two')
    expect(rows[1].right?.line).toBe('TWO')
    // Inline token diff comes from the conflict-resolver engine, unchanged.
    expect(rows[1].right?.tokens.some((token) => token.status === 'changed')).toBe(true)
  })

  it('numbers lines correctly after an insertion shifts the new side', () => {
    const { rows } = buildDiffRows(['a', 'b'], ['a', 'inserted', 'b'])
    const added = rows.filter((row) => row.status === 'added')
    expect(added).toHaveLength(1)
    expect(added[0].leftNumber).toBeNull()
    expect(added[0].rightNumber).toBe(2)
    const last = rows[rows.length - 1]
    expect(last.status).toBe('common')
    expect(last.leftNumber).toBe(2)
    expect(last.rightNumber).toBe(3)
  })

  it('reports a deletion on the old side only', () => {
    const { rows } = buildDiffRows(['a', 'gone', 'b'], ['a', 'b'])
    const removed = rows.filter((row) => row.status === 'removed')
    expect(removed).toHaveLength(1)
    expect(removed[0].leftNumber).toBe(2)
    expect(removed[0].rightNumber).toBeNull()
    expect(removed[0].right).toBeNull()
  })

  it('treats a new file (no old side) as all-added', () => {
    const { rows } = buildDiffRows([], ['a', 'b'])
    expect(rows.map((row) => row.status)).toEqual(['added', 'added'])
    expect(rows.map((row) => row.rightNumber)).toEqual([1, 2])
  })

  it('treats a deleted file (no new side) as all-removed', () => {
    const { rows } = buildDiffRows(['a', 'b'], [])
    expect(rows.map((row) => row.status)).toEqual(['removed', 'removed'])
    expect(rows.map((row) => row.leftNumber)).toEqual([1, 2])
  })

  it('produces only common rows for identical content', () => {
    const { rows } = buildDiffRows(['a', 'b'], ['a', 'b'])
    expect(rows.every((row) => row.status === 'common')).toBe(true)
    expect(diffStats(rows)).toEqual({ added: 0, removed: 0, changed: 0 })
  })

  it('degrades to a flagged approximate diff instead of running an unbounded LCS', () => {
    const oldLines = Array.from({ length: MAX_LCS_LINES + 10 }, (_, i) => `old ${i}`)
    const newLines = Array.from({ length: MAX_LCS_LINES + 10 }, (_, i) => `new ${i}`)
    const { rows, approximate } = buildDiffRows(oldLines, newLines)
    expect(approximate).toBe(true)
    const stats = diffStats(rows)
    expect(stats.removed).toBe(oldLines.length)
    expect(stats.added).toBe(newLines.length)
    expect(stats.changed).toBe(0)
  })

  it('trims the identical head/tail so a big file with a small edit still diffs exactly', () => {
    const prefix = Array.from({ length: MAX_LCS_LINES }, (_, i) => `line ${i}`)
    const suffix = Array.from({ length: MAX_LCS_LINES }, (_, i) => `tail ${i}`)
    const { rows, approximate } = buildDiffRows(
      [...prefix, 'before', ...suffix],
      [...prefix, 'after', ...suffix],
    )
    expect(approximate).toBe(false)
    const changed = rows.filter((row) => row.status === 'changed')
    expect(changed).toHaveLength(1)
    expect(changed[0].leftNumber).toBe(MAX_LCS_LINES + 1)
  })
})

describe('toUnifiedRows', () => {
  it('expands a changed row into the old then new patch lines', () => {
    const { rows } = buildDiffRows(['keep', 'two'], ['keep', 'TWO'])
    const unified = toUnifiedRows(rows)
    expect(unified.map((row) => row.sign)).toEqual([' ', '-', '+'])
    expect(unified[1].line.line).toBe('two')
    expect(unified[1].rightNumber).toBeNull()
    expect(unified[2].line.line).toBe('TWO')
    expect(unified[2].leftNumber).toBeNull()
  })
})

describe('collapseCommonRows', () => {
  it('keeps the requested context around a change and folds the rest into a gap', () => {
    const oldLines = Array.from({ length: 30 }, (_, i) => `line ${i}`)
    const newLines = [...oldLines]
    newLines[15] = 'edited'
    const sections = collapseCommonRows(buildDiffRows(oldLines, newLines).rows, 2)
    const gaps = sections.filter((section) => section.kind === 'gap')
    const shown = sections.flatMap((section) => (section.kind === 'rows' ? section.rows : []))
    // 2 context lines each side + the changed row itself.
    expect(shown).toHaveLength(5)
    expect(shown.filter((row) => row.status === 'changed')).toHaveLength(1)
    expect(gaps).toHaveLength(2)
    expect(gaps.reduce((sum, gap) => sum + (gap.kind === 'gap' ? gap.count : 0), 0)).toBe(25)
  })

  it('collapses an unchanged file into a single gap', () => {
    const rows = buildDiffRows(['a', 'b', 'c'], ['a', 'b', 'c']).rows
    expect(collapseCommonRows(rows, 2)).toEqual([{ kind: 'gap', count: 3 }])
  })
})
