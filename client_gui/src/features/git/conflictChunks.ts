// flowgate.v02gui.0004 — the direct resolver's view of one conflict file. Splits the working file
// the conflicts answer carries into alternating common text and marker chunks exactly as the
// server does (services/git/conflict.py `_split_content_segments`: same markers, same order, a
// malformed file = null), so chunk N here is chunk N of POST …/resolve `files[].chunks`.

export interface ConflictChunk {
  /** 1-based, file order — the number the resolve request carries. */
  number: number
  ours: string[]
  /** The common ancestor section (`|||||||`), null when the merge wrote none. */
  base: string[] | null
  theirs: string[]
}

export type ConflictSegment = { kind: 'common'; lines: string[] } | { kind: 'chunk'; chunk: ConflictChunk }

const OPEN = /^<{7}( |$)/
const CLOSE = /^>{7}( |$)/
const SEP = /^={7}$/
const BASE = /^\|{7}( |$)/

/** Python `str.splitlines()` for the line breaks a source file has (\n, \r\n, \r). */
function splitLines(content: string): string[] {
  if (!content) return []
  const lines = content.split(/\r\n|\r|\n/)
  if (/(\r\n|\r|\n)$/.test(content)) lines.pop()
  return lines
}

/** Segments of `content`, or null when its markers are unbalanced or nested (the server refuses those too). */
export function splitConflictSegments(content: string): ConflictSegment[] | null {
  const segments: ConflictSegment[] = []
  let common: string[] = []
  let state: 'common' | 'ours' | 'base' | 'theirs' = 'common'
  let chunk: ConflictChunk | null = null
  let number = 0
  for (const line of splitLines(content)) {
    if (state === 'common') {
      if (OPEN.test(line)) {
        segments.push({ kind: 'common', lines: common })
        common = []
        number += 1
        chunk = { number, ours: [], base: null, theirs: [] }
        state = 'ours'
      } else common.push(line)
    } else if (state === 'ours') {
      if (BASE.test(line)) {
        chunk!.base = []
        state = 'base'
      } else if (SEP.test(line)) state = 'theirs'
      else if (OPEN.test(line) || CLOSE.test(line)) return null
      else chunk!.ours.push(line)
    } else if (state === 'base') {
      if (SEP.test(line)) state = 'theirs'
      else if (OPEN.test(line) || CLOSE.test(line)) return null
      else chunk!.base!.push(line)
    } else {
      if (CLOSE.test(line)) {
        segments.push({ kind: 'chunk', chunk: chunk! })
        chunk = null
        state = 'common'
      } else if (OPEN.test(line) || SEP.test(line) || BASE.test(line)) return null
      else chunk!.theirs.push(line)
    }
  }
  if (state !== 'common') return null
  segments.push({ kind: 'common', lines: common })
  return segments
}

export function conflictChunks(segments: readonly ConflictSegment[] | null): ConflictChunk[] {
  return (segments ?? []).flatMap((segment) => (segment.kind === 'chunk' ? [segment.chunk] : []))
}

/** How a chunk was settled in the editor: one side, both (current first), or typed by hand. */
export type ChunkChoice = 'ours' | 'theirs' | 'both' | 'manual'

export function chunkText(chunk: ConflictChunk, choice: Exclude<ChunkChoice, 'manual'>): string {
  const lines = choice === 'ours' ? chunk.ours : choice === 'theirs' ? chunk.theirs : [...chunk.ours, ...chunk.theirs]
  return lines.join('\n')
}
