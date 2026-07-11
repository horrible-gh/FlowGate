import { describe, expect, it } from 'vitest'
import {
  applyChunkChoice,
  chunkIndexes,
  parseConflictFile,
  recommendChunkChoice,
  resetChunkChoice,
  unresolvedChunkCount,
  type ChunkSegment,
  type ConflictFileState,
} from '../useConflictChunks'

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
})