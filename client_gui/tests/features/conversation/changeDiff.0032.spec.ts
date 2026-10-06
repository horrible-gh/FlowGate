import { describe, expect, it } from 'vitest'
import { diffLines, diffUnavailableReason } from '@/features/conversation/changeDiff'
import type { RunFileDiff, RunFileDiffSide } from '@/features/conversation/types'

// flowgate.v02.0032 — line diff of a stored run change file (D0006 §3.8.3, §6.1).
const render = (old: string | null, next: string | null, maxCells?: number) =>
  diffLines(old, next, maxCells).map((l) => `${l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : ' '}${l.text}`)

describe('diffLines', () => {
  it('keeps common lines as context and marks the changed ones', () => {
    expect(render('a\nb\nc\n', 'a\nB\nc\nd\n')).toEqual([' a', '-b', '+B', ' c', '+d'])
  })

  it('numbers old and new lines separately', () => {
    const lines = diffLines('a\nb\n', 'x\na\nb\n')
    expect(lines.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ['add', null, 1],
      ['context', 1, 2],
      ['context', 2, 3],
    ])
  })

  it('shows an added file as all added and a deleted file as all removed', () => {
    expect(render(null, 'one\ntwo')).toEqual(['+one', '+two'])
    expect(render('one\n', null)).toEqual(['-one'])
  })

  it('treats CRLF like LF and a final newline as the end of the last line', () => {
    expect(render('a\r\nb\r\n', 'a\nb\n')).toEqual([' a', ' b'])
  })

  it('aligns the middle by common subsequence', () => {
    expect(render('h\n1\n2\n3\nt\n', 'h\n2\n3\n4\nt\n')).toEqual([' h', '-1', ' 2', ' 3', '+4', ' t'])
  })

  it('falls back to removed-then-added when the middle is too large to align', () => {
    expect(render('h\n1\n2\nt\n', 'h\n2\n1\nt\n', 1)).toEqual([' h', '-1', '-2', '+2', '+1', ' t'])
  })

  it('returns nothing for two equal sides', () => {
    expect(render('same\n', 'same\n').every((l) => l.startsWith(' '))).toBe(true)
    expect(render(null, null)).toEqual([])
  })
})

describe('diffUnavailableReason', () => {
  const side = (extra: Partial<RunFileDiffSide> = {}): RunFileDiffSide => ({ exists: true, binary: false, truncated: false, size: 1, content: 'x', ...extra })
  const diff = (old: RunFileDiffSide, next: RunFileDiffSide): RunFileDiff => ({ runId: 'r', path: 'p', status: 'M', old, new: next })

  it('names binary and oversize sides, and nothing for text', () => {
    expect(diffUnavailableReason(diff(side(), side({ binary: true, content: null })))).toBe('binary')
    expect(diffUnavailableReason(diff(side({ truncated: true }), side()))).toBe('too_large')
    expect(diffUnavailableReason(diff(side(), side()))).toBeNull()
  })
})
