import { describe, expect, it } from 'vitest'
import {
  applyChunkChoice,
  buildChunkSideDiff,
  chunkIndexes,
  parseConflictFile,
  recommendChunkChoice,
  resetChunkChoice,
  unresolvedChunkCount,
  type ChunkSegment,
  type ConflictFileState,
} from '@main/composables/useConflictChunks'

function chunk(ours: string[], theirs: string[], base: string[] = [], hasBase = false): ChunkSegment {
  return {
    kind: 'chunk',
    openLine: '<<<<<<< ours\n',
    baseLine: hasBase ? '||||||| base\n' : null,
    sepLine: '=======\n',
    closeLine: '>>>>>>> theirs\n',
    ours,
    base,
    theirs,
    oursLabel: 'ours',
    theirsLabel: 'theirs',
    choice: null,
    resolution: null,
  }
}

function fileWith(...chunks: ChunkSegment[]): ConflictFileState {
  return {
    path: 'sample.txt',
    conflict_count: chunks.length,
    directText: '',
    mode: 'chunk',
    segments: chunks,
    notice: '',
  }
}

describe('conflict chunk recommendations', () => {
  it('recommends the side changed from a diff3 base', () => {
    expect(recommendChunkChoice(chunk(['old\n'], ['new\n'], ['old\n'], true))).toBe('theirs')
    expect(recommendChunkChoice(chunk(['new\n'], ['old\n'], ['old\n'], true))).toBe('ours')
  })

  it('handles identical and empty sides, but defers ambiguous choices', () => {
    expect(recommendChunkChoice(chunk(['same\n'], ['same\n']))).toBe('ours')
    expect(recommendChunkChoice(chunk([], ['added\n']))).toBe('theirs')
    expect(recommendChunkChoice(chunk(['left\n'], []))).toBe('ours')
    expect(recommendChunkChoice(chunk(['left\n'], ['right\n']))).toBeNull()
  })

  it('supports apply, counts, and undo without losing source sides', () => {
    const first = chunk(['ours\n'], ['theirs\n'])
    const second = chunk(['same\n'], ['same\n'])
    const file = fileWith(first, second)
    file.segments.splice(1, 0, { kind: 'common', lines: ['context\n'] })

    expect(chunkIndexes(file)).toEqual([0, 2])
    expect(unresolvedChunkCount(file)).toBe(2)

    applyChunkChoice(first, 'both')
    expect(first.resolution).toEqual(['ours\n', 'theirs\n'])
    expect(unresolvedChunkCount(file)).toBe(1)

    resetChunkChoice(first)
    expect(first.choice).toBeNull()
    expect(first.resolution).toBeNull()
    expect(first.ours).toEqual(['ours\n'])
  })

  it('keeps parser output compatible with recommendation helpers', () => {
    const parsed = parseConflictFile(
      'before\n<<<<<<< ours\nold\n||||||| base\nold\n=======\nnew\n>>>>>>> theirs\nafter\n',
    )
    const parsedChunk = parsed?.find((segment): segment is ChunkSegment => segment.kind === 'chunk')
    expect(parsedChunk).toBeTruthy()
    expect(recommendChunkChoice(parsedChunk!)).toBe('theirs')
  })

  it('correctly parses zdiff3 base section and populates baseLine and base array', () => {
    // Test with actual zdiff3 marker format including base content
    const zdiff3Content = 'before\n<<<<<<< HEAD\nmainline version\n||||||| line1\nline1\n=======\ngroup version\n>>>>>>> group/branch\nafter\n'
    const parsed = parseConflictFile(zdiff3Content)
    const parsedChunk = parsed?.find((segment): segment is ChunkSegment => segment.kind === 'chunk')

    expect(parsedChunk).toBeTruthy()
    // Verify baseLine is non-null when base marker is present
    expect(parsedChunk!.baseLine).not.toBeNull()
    expect(parsedChunk!.baseLine).toBe('||||||| line1\n')
    // Verify base array contains the common ancestor content (with newlines preserved)
    expect(parsedChunk!.base).toEqual(['line1\n'])
    // Verify ours/theirs content is correct
    expect(parsedChunk!.ours).toEqual(['mainline version\n'])
    expect(parsedChunk!.theirs).toEqual(['group version\n'])
  })
  it('builds display-only line and token diffs without changing chunk source lines', () => {
    const seg = chunk(['same\n', 'value = 1\n', 'ours only\n'], ['same\n', 'value = 2\n', 'theirs only\n'])
    const diff = buildChunkSideDiff(seg.ours, seg.theirs)

    expect(diff.ours.map((line) => line.status)).toEqual(['common', 'changed', 'changed'])
    expect(diff.theirs.map((line) => line.status)).toEqual(['common', 'changed', 'changed'])
    expect(diff.ours[1].tokens.some((token) => token.status === 'changed' && token.text === '1')).toBe(true)
    expect(diff.theirs[1].tokens.some((token) => token.status === 'changed' && token.text === '2')).toBe(true)
    expect(seg.ours).toEqual(['same\n', 'value = 1\n', 'ours only\n'])
    expect(seg.theirs).toEqual(['same\n', 'value = 2\n', 'theirs only\n'])
  })

  it('marks unmatched lines as removed or added in display diffs', () => {
    const diff = buildChunkSideDiff(['same\n', 'old\n'], ['same\n', 'new\n', 'extra\n'])

    expect(diff.ours.map((line) => line.status)).toEqual(['common', 'changed'])
    expect(diff.theirs.map((line) => line.status)).toEqual(['common', 'changed', 'added'])
    expect(diff.theirs[2].tokens).toEqual([{ text: 'extra', status: 'added' }])
  })
})
// A minimal, self-contained reproduction of the pre-change buildChunkSideDiff:
// a single lcsPairs() call across the *entire* ours/theirs arrays, with no
// anchor pre-pass. Kept local to this test file (not imported from the
// source) so this regression proof still stands even after the old code
// path is gone from useConflictChunks.ts.
function legacyLcsPairs(left: string[], right: string[]): Array<[number, number]> {
  const dp = Array.from({ length: left.length + 1 }, () => new Array<number>(right.length + 1).fill(0))
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const pairs: Array<[number, number]> = []
  let i = 0
  let j = 0
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      pairs.push([i, j])
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i += 1
    } else {
      j += 1
    }
  }
  return pairs
}

function legacyStatuses(oursLines: string[], theirsLines: string[]): { ours: string[]; theirs: string[] } {
  const pairs = legacyLcsPairs(oursLines, theirsLines)
  const ours: string[] = []
  const theirs: string[] = []
  let oursPos = 0
  let theirsPos = 0

  function appendRun(oursRun: string[], theirsRun: string[]) {
    const paired = Math.min(oursRun.length, theirsRun.length)
    for (let i = 0; i < paired; i += 1) {
      ours.push('changed')
      theirs.push('changed')
    }
    for (let i = paired; i < oursRun.length; i += 1) ours.push('removed')
    for (let i = paired; i < theirsRun.length; i += 1) theirs.push('added')
  }

  for (const [nextOurs, nextTheirs] of pairs) {
    appendRun(oursLines.slice(oursPos, nextOurs), theirsLines.slice(theirsPos, nextTheirs))
    ours.push('common')
    theirs.push('common')
    oursPos = nextOurs + 1
    theirsPos = nextTheirs + 1
  }
  appendRun(oursLines.slice(oursPos), theirsLines.slice(theirsPos))
  return { ours, theirs }
}

describe('line anchor stability for repeated lines (R0001)', () => {
  it('Case A: proves the pre-change whole-array LCS misclassifies a real common line, then confirms the anchor fix keeps it common', () => {
    // "unique body A" sits before a run of 3 blank lines on ours but after an
    // equal-length run of 3 blank lines on theirs. Matching "unique body A"
    // would force the strictly-increasing index constraint to drop the longer
    // blank-line run, so a single whole-array LCS picks the longer match and
    // sacrifices "unique body A" instead -- exactly the R0001 shape (Markdown
    // blank-line noise displacing a real line that exists unchanged on both
    // sides), plus a genuinely changed body line for contrast.
    const ours = [
      'unique body A\n', '\n', '\n', '\n',
      'unique body B\n',
      '\n',
      'changed body old\n',
      '\n',
      'unique body C\n',
    ]
    const theirs = [
      '\n', '\n', '\n',
      'unique body A\n',
      'unique body B\n',
      '\n',
      'changed body new\n',
      '\n',
      'unique body C\n',
    ]

    // Pre-change proof: the old whole-array LCS gets this wrong.
    const legacy = legacyStatuses(ours, theirs)
    expect(legacy.ours[0]).not.toBe('common') // "unique body A" wrongly treated as removed
    expect(legacy.theirs[3]).not.toBe('common') // ...and wrongly treated as added on theirs

    // Post-change behavior: the anchor pass keeps it common.
    const diff = buildChunkSideDiff(ours, theirs)
    const oursByLine = new Map(diff.ours.map((l) => [l.line, l.status]))
    const theirsByLine = new Map(diff.theirs.map((l) => [l.line, l.status]))
    expect(oursByLine.get('unique body A\n')).toBe('common')
    expect(theirsByLine.get('unique body A\n')).toBe('common')
    expect(oursByLine.get('unique body B\n')).toBe('common')
    expect(oursByLine.get('unique body C\n')).toBe('common')
    expect(theirsByLine.get('unique body C\n')).toBe('common')
    expect(oursByLine.get('changed body old\n')).not.toBe('common')
    expect(theirsByLine.get('changed body new\n')).not.toBe('common')
  })

  it('Case B: isolates two narrow change regions between repeated headings/separators', () => {
    const ours = [
      '## Section\n', '---\n', 'body one old\n', '---\n', '## Section\n', '---\n', 'unchanged middle\n', '---\n',
      '## Section\n', '---\n', 'body two old\n', '---\n',
    ]
    const theirs = [
      '## Section\n', '---\n', 'body one new\n', '---\n', '## Section\n', '---\n', 'unchanged middle\n', '---\n',
      '## Section\n', '---\n', 'body two new\n', '---\n',
    ]
    const diff = buildChunkSideDiff(ours, theirs)

    const oursByLine = new Map(diff.ours.map((l) => [l.line, l.status]))
    const theirsByLine = new Map(diff.theirs.map((l) => [l.line, l.status]))
    expect(oursByLine.get('unchanged middle\n')).toBe('common')
    expect(theirsByLine.get('unchanged middle\n')).toBe('common')
    expect(oursByLine.get('body one old\n')).not.toBe('common')
    expect(theirsByLine.get('body one new\n')).not.toBe('common')
    expect(oursByLine.get('body two old\n')).not.toBe('common')
    expect(theirsByLine.get('body two new\n')).not.toBe('common')
  })

  it('Case C: falls back to plain LCS without crashing when no unique anchor exists', () => {
    const ours = ['\n', '-\n', '\n', '-\n', '\n']
    const theirs = ['\n', '-\n', '\n', '-\n', '\n', '-\n']

    expect(() => buildChunkSideDiff(ours, theirs)).not.toThrow()
    const diff = buildChunkSideDiff(ours, theirs)
    expect(diff.ours).toHaveLength(5)
    expect(diff.theirs.length).toBeGreaterThanOrEqual(5)
  })

  it('Case D: leaves a simple single-line change unaffected', () => {
    const diff = buildChunkSideDiff(['keep\n', 'old\n'], ['keep\n', 'new\n'])
    expect(diff.ours.map((l) => l.status)).toEqual(['common', 'changed'])
    expect(diff.theirs.map((l) => l.status)).toEqual(['common', 'changed'])
  })

  it('Case E: display diff improvements do not change chunk choice assembly', () => {
    const seg = chunk(
      ['unique body A\n', '\n', 'unique body B\n', '\n', 'unique body C\n'],
      ['unique body A\n', '\n', 'new line\n', '\n', 'unique body B\n', '\n', 'unique body C\n'],
    )
    buildChunkSideDiff(seg.ours, seg.theirs)

    applyChunkChoice(seg, 'ours')
    expect(seg.resolution).toEqual(seg.ours)
    applyChunkChoice(seg, 'theirs')
    expect(seg.resolution).toEqual(seg.theirs)
    applyChunkChoice(seg, 'both')
    expect(seg.resolution).toEqual([...seg.ours, ...seg.theirs])
  })
})
