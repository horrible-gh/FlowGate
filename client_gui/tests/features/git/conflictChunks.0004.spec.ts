import { describe, expect, it } from 'vitest'
import { chunkText, conflictChunks, splitConflictSegments } from '@/features/git/conflictChunks'

// flowgate.v02gui.0004 — the direct resolver numbers chunks exactly as the server's
// `_split_content_segments` does, so chunk N is chunk N of POST …/resolve.
describe('conflictChunks', () => {
  it('alternates common text and chunks, numbered in file order, with or without a base section', () => {
    const segments = splitConflictSegments(
      'a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> flow\nb\n<<<<<<< HEAD\np\n||||||| base\no\n=======\nq\n>>>>>>> flow\n',
    )
    expect(segments?.map((s) => s.kind)).toEqual(['common', 'chunk', 'common', 'chunk', 'common'])
    const chunks = conflictChunks(segments)
    expect(chunks.map((c) => c.number)).toEqual([1, 2])
    expect(chunks[0]).toEqual({ number: 1, ours: ['x'], base: null, theirs: ['y'] })
    expect(chunks[1]).toEqual({ number: 2, ours: ['p'], base: ['o'], theirs: ['q'] })
    expect(chunkText(chunks[0], 'both')).toBe('x\ny')
    expect(chunkText(chunks[0], 'theirs')).toBe('y')
  })

  it('CRLF files split the same way', () => {
    expect(conflictChunks(splitConflictSegments('<<<<<<< a\r\nx\r\n=======\r\ny\r\n>>>>>>> b\r\n'))).toEqual([
      { number: 1, ours: ['x'], base: null, theirs: ['y'] },
    ])
  })

  it('unbalanced or nested markers are malformed (null), as the server refuses them', () => {
    expect(splitConflictSegments('<<<<<<< a\nx\n=======\ny\n')).toBeNull()
    expect(splitConflictSegments('<<<<<<< a\n<<<<<<< b\n=======\n>>>>>>> c\n')).toBeNull()
    expect(splitConflictSegments('plain\n')).toEqual([{ kind: 'common', lines: ['plain'] }])
  })
})
