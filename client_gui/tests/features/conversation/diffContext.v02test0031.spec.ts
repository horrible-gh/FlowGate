import { describe, expect, it } from 'vitest'
import { diffLines, diffLinesDetailed } from '@/features/conversation/changeDiff'
import { projectDiffContext, type DiffContextRow } from '@/features/conversation/diffContext'

const gaps = (rows: DiffContextRow[]) => rows.filter((row) => row.type === 'gap')
const visible = (rows: DiffContextRow[]) => rows.flatMap((row) => row.type === 'line' ? [row.line] : [])
function replacement(size: number, ...positions: number[]) {
  const old = Array.from({ length: size }, (_, i) => `line${i + 1}`)
  const next = old.map((text, i) => positions.includes(i + 1) ? `changed${i + 1}` : text)
  return diffLines(old.join('\n'), next.join('\n'))
}

describe('projectDiffContext', () => {
  it('keeps exactly three context lines around a replacement and exact edge ranges', () => {
    const rows = projectDiffContext(replacement(100, 50))
    expect(gaps(rows)).toEqual([
      { type: 'gap', count: 46, oldStart: 1, oldEnd: 46, newStart: 1, newEnd: 46 },
      { type: 'gap', count: 47, oldStart: 54, oldEnd: 100, newStart: 54, newEnd: 100 },
    ])
    expect(visible(rows).map((line) => [line.kind, line.oldNo, line.newNo])).toEqual([
      ['context', 47, 47], ['context', 48, 48], ['context', 49, 49],
      ['del', 50, null], ['add', null, 50],
      ['context', 51, 51], ['context', 52, 52], ['context', 53, 53],
    ])
    expect(rows).toHaveLength(10)
  })

  it('retains a late change in a 3000-line file', () => {
    const rows = projectDiffContext(replacement(3000, 2500))
    expect(visible(rows).filter((line) => line.kind !== 'context').map((line) => line.text))
      .toEqual(['line2500', 'changed2500'])
    expect(gaps(rows).map((row) => row.count)).toEqual([2496, 497])
    expect(rows).toHaveLength(10)
  })

  it.each([1, 100])('does not invent an edge gap at changed line %i', (position) => {
    const rows = projectDiffContext(replacement(100, position))
    expect(gaps(rows).map((row) => row.count)).toEqual([96])
    expect(rows[position === 1 ? 0 : rows.length - 1].type).toBe('line')
  })

  it.each([1, 4, 7])('merges overlapping or touching windows at distance %i', (distance) => {
    const rows = projectDiffContext(replacement(40, 10, 10 + distance))
    expect(gaps(rows)).toHaveLength(2) // only the leading and trailing gaps
    expect(visible(rows).filter((line) => line.kind !== 'context')).toHaveLength(4)
  })

  it('retains one gap between separated windows, including the one-line boundary', () => {
    expect(gaps(projectDiffContext(replacement(40, 10, 18))).map((row) => row.count))
      .toEqual([6, 1, 19])
    expect(gaps(projectDiffContext(replacement(100, 20, 80))).map((row) => row.count))
      .toEqual([16, 53, 17])
  })

  it('preserves identities, shifted old/new numbers and immutable input', () => {
    const old = Array.from({ length: 20 }, (_, i) => `line${i + 1}`)
    const next = ['inserted', ...old]
    const lines = diffLines(old.join('\n'), next.join('\n'))
    const before = JSON.stringify(lines)
    lines.forEach(Object.freeze)
    Object.freeze(lines)
    const rows = projectDiffContext(lines)
    expect(visible(rows).every((line) => lines.includes(line))).toBe(true)
    expect(visible(rows).map((line) => [line.oldNo, line.newNo])).toEqual([
      [null, 1], [1, 2], [2, 3], [3, 4],
    ])
    expect(gaps(rows)).toEqual([
      { type: 'gap', count: 17, oldStart: 4, oldEnd: 20, newStart: 5, newEnd: 21 },
    ])
    expect(JSON.stringify(lines)).toBe(before)
  })

  it('keeps all additions/deletions without a 2000-row cap', () => {
    const text = Array.from({ length: 3000 }, (_, i) => `line${i}`).join('\n')
    for (const [old, next, kind] of [[null, text, 'add'], [text, null, 'del']] as const) {
      const lines = diffLines(old, next)
      const rows = projectDiffContext(lines)
      expect(gaps(rows)).toEqual([])
      expect(visible(rows)).toEqual(lines)
      expect(rows).toHaveLength(3000)
      expect(visible(rows).every((line) => line.kind === kind)).toBe(true)
    }
  })

  it('returns empty for unchanged, empty and newline-normalized inputs', () => {
    for (const [old, next] of [['same', 'same'], [null, null], ['', ''], ['a\r\n', 'a\n']] as const) {
      expect(projectDiffContext(diffLines(old, next))).toEqual([])
    }
  })

  it('supports zero and custom context without changing whitespace differences', () => {
    expect(gaps(projectDiffContext(replacement(100, 50), 0)).map((row) => row.count)).toEqual([49, 50])
    expect(visible(projectDiffContext(replacement(100, 50), 1))).toHaveLength(4)
    expect(gaps(projectDiffContext(replacement(100, 50), 100))).toEqual([])
    expect(visible(projectDiffContext(diffLines('a ', 'a'))).map((line) => line.text)).toEqual(['a ', 'a'])
  })

  it.each([-1, 1.5, NaN, Infinity])('rejects invalid context %s', (context) => {
    expect(() => projectDiffContext([], context)).toThrow(RangeError)
  })
})

describe('diffLinesDetailed', () => {
  it.each([1, 3, 4, 5])('reports the trimmed middle 2x2 cell boundary at maxCells=%i', (maxCells) => {
    const old = 'h\n1\n2\nt'
    const next = 'h\n2\n1\nt'
    const result = diffLinesDetailed(old, next, maxCells)
    expect(result.aligned).toBe(maxCells >= 4)
    expect(result.lines).toEqual(diffLines(old, next, maxCells))
    expect(result.lines.map((line) => `${line.kind}:${line.text}`)).toEqual(maxCells >= 4
      ? ['context:h', 'del:1', 'context:2', 'add:1', 'context:t']
      : ['context:h', 'del:1', 'del:2', 'add:2', 'add:1', 'context:t'])
  })

  it('reports aligned for identical, empty, added and removed files', () => {
    for (const [old, next] of [['a', 'a'], [null, null], [null, 'a'], ['a', null]] as const) {
      expect(diffLinesDetailed(old, next, 0).aligned).toBe(true)
    }
  })
})
