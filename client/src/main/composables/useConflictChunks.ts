// flowgate.default.0182 NR0003 §6 — conflict chunk resolution logic, extracted
// verbatim from GitFinalizePanel (0167) so the header Git status panel's inline
// resolver offers the same button-based chunk workflow instead of a raw
// textarea. Pure parser/assembler state machine + per-file view-state helpers;
// the hosting component owns fetching, submission and layout.
import { useI18n } from 'vue-i18n'

export const MAX_CHUNK_VIEW_CHARS = 500000
export const MAX_MARKER_REPORT = 5
// A bare "=======" is not counted as a marker on its own (it doubles as a
// Markdown H1 underline); only the <<<<<<< / >>>>>>> pair gates resolution.
export const MARKER_OPEN_RE = /^<{7}( |$)/
export const MARKER_CLOSE_RE = /^>{7}( |$)/
export const MARKER_SEP_RE = /^={7}$/
export const MARKER_BASE_RE = /^\|{7}( |$)/

export type ConflictMode = 'chunk' | 'direct' | 'direct_only'
export type ChunkChoice = 'ours' | 'theirs' | 'both' | null

export type CommonSegment = { kind: 'common'; lines: string[] }
export type ChunkSegment = {
  kind: 'chunk'
  openLine: string
  baseLine: string | null
  sepLine: string
  closeLine: string
  ours: string[]
  base: string[]
  theirs: string[]
  oursLabel: string
  theirsLabel: string
  choice: ChunkChoice
  resolution: string[] | null
}
export type ConflictSegment = CommonSegment | ChunkSegment

export interface ConflictFileState {
  path: string
  conflict_count: number
  directText: string
  mode: ConflictMode
  segments: ConflictSegment[]
  notice: string
}

function splitKeepEol(content: string): string[] {
  if (!content) return []
  const matches = content.match(/.*(?:\r\n|\n|\r|$)/g) || []
  return matches.filter((line, index) => line !== '' || index < matches.length - 1)
}
function stripEol(line: string): string {
  return line.replace(/\r\n$|\n$|\r$/, '')
}
function markerLabel(line: string, prefix: string): string {
  const s = stripEol(line)
  return s.startsWith(prefix) ? s.slice(prefix.length).trim() : ''
}
function pushCommon(segments: ConflictSegment[], lines: string[]) {
  if (lines.length) segments.push({ kind: 'common', lines: [...lines] })
  lines.length = 0
}

export function parseConflictFile(content: string): ConflictSegment[] | null {
  const lines = splitKeepEol(content)
  const segments: ConflictSegment[] = []
  const common: string[] = []
  let stateName: 'COMMON' | 'OURS' | 'BASE' | 'THEIRS' = 'COMMON'
  let chunk: ChunkSegment | null = null

  for (const line of lines) {
    const s = stripEol(line)
    if (stateName === 'COMMON') {
      if (MARKER_OPEN_RE.test(s)) {
        pushCommon(segments, common)
        chunk = {
          kind: 'chunk',
          openLine: line,
          baseLine: null,
          sepLine: '',
          closeLine: '',
          ours: [],
          base: [],
          theirs: [],
          oursLabel: markerLabel(line, '<<<<<<< '),
          theirsLabel: '',
          choice: null,
          resolution: null,
        }
        stateName = 'OURS'
      } else {
        common.push(line)
      }
    } else if (stateName === 'OURS') {
      if (!chunk) return null
      if (MARKER_BASE_RE.test(s)) {
        chunk.baseLine = line
        stateName = 'BASE'
      } else if (MARKER_SEP_RE.test(s)) {
        chunk.sepLine = line
        stateName = 'THEIRS'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_CLOSE_RE.test(s)) {
        return null
      } else {
        chunk.ours.push(line)
      }
    } else if (stateName === 'BASE') {
      if (!chunk) return null
      if (MARKER_SEP_RE.test(s)) {
        chunk.sepLine = line
        stateName = 'THEIRS'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_CLOSE_RE.test(s)) {
        return null
      } else {
        chunk.base.push(line)
      }
    } else if (stateName === 'THEIRS') {
      if (!chunk) return null
      if (MARKER_CLOSE_RE.test(s)) {
        chunk.closeLine = line
        chunk.theirsLabel = markerLabel(line, '>>>>>>> ')
        segments.push(chunk)
        chunk = null
        stateName = 'COMMON'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_SEP_RE.test(s) || MARKER_BASE_RE.test(s)) {
        return null
      } else {
        chunk.theirs.push(line)
      }
    }
  }

  if (stateName !== 'COMMON') return null
  pushCommon(segments, common)
  return segments
}

export function assembleFile(segments: ConflictSegment[]): string {
  const out: string[] = []
  for (const seg of segments) {
    if (seg.kind === 'common') {
      out.push(...seg.lines)
    } else if (seg.resolution) {
      out.push(...seg.resolution)
    } else {
      out.push(seg.openLine, ...seg.ours)
      if (seg.baseLine) out.push(seg.baseLine, ...seg.base)
      out.push(seg.sepLine, ...seg.theirs, seg.closeLine)
    }
  }
  return out.join('')
}

export function residualMarkers(content: string): number[] {
  const result: number[] = []
  const lines = content.split(/\r\n|\n|\r/)
  lines.forEach((line, index) => {
    if (result.length >= MAX_MARKER_REPORT) return
    if (MARKER_OPEN_RE.test(line) || MARKER_CLOSE_RE.test(line)) result.push(index + 1)
  })
  return result
}

export function currentFileContent(file: ConflictFileState): string {
  return file.mode === 'chunk' ? assembleFile(file.segments) : file.directText
}

export function isFileResolved(file: ConflictFileState): boolean {
  return residualMarkers(currentFileContent(file)).length === 0
}

export function joinLines(lines: string[]): string {
  return lines.join('')
}

export function applyChunkChoice(seg: ChunkSegment, choice: Exclude<ChunkChoice, null>) {
  seg.choice = choice
  if (choice === 'ours') seg.resolution = [...seg.ours]
  else if (choice === 'theirs') seg.resolution = [...seg.theirs]
  else seg.resolution = [...seg.ours, ...seg.theirs]
}

export function chunkNumber(file: ConflictFileState, segmentIndex: number): number {
  return file.segments.slice(0, segmentIndex + 1).filter((seg) => seg.kind === 'chunk').length
}

export function chunkLabel(label: string, fallback: string): string {
  return label || fallback
}

// i18n-dependent helpers (notices on parse fallbacks) live in the composable.
export function useConflictChunks() {
  const { t } = useI18n()

  function initConflictFile(f: {
    path: string
    content: string
    conflict_count: number
  }): ConflictFileState {
    if (f.content.length > MAX_CHUNK_VIEW_CHARS) {
      return {
        path: f.path,
        conflict_count: f.conflict_count,
        directText: f.content,
        mode: 'direct_only',
        segments: [],
        notice: t('main.git_finalize.too_large_direct'),
      }
    }
    const parsed = parseConflictFile(f.content)
    const chunkCount = parsed ? parsed.filter((seg) => seg.kind === 'chunk').length : 0
    if (!parsed || (f.conflict_count > 0 && chunkCount === 0)) {
      return {
        path: f.path,
        conflict_count: f.conflict_count,
        directText: f.content,
        mode: 'direct_only',
        segments: [],
        notice: t('main.git_finalize.direct_only_notice'),
      }
    }
    return {
      path: f.path,
      conflict_count: f.conflict_count,
      directText: f.content,
      mode: 'chunk',
      segments: parsed,
      notice: '',
    }
  }

  function switchToDirectEdit(file: ConflictFileState) {
    file.directText = assembleFile(file.segments)
    file.mode = 'direct'
    file.notice = ''
  }

  function switchToChunkView(file: ConflictFileState) {
    const parsed = parseConflictFile(file.directText)
    if (!parsed) {
      file.notice = t('main.git_finalize.switch_parse_failed')
      return
    }
    file.segments = parsed
    file.mode = 'chunk'
    file.notice = ''
  }

  return {
    initConflictFile,
    switchToDirectEdit,
    switchToChunkView,
    parseConflictFile,
    assembleFile,
    residualMarkers,
    currentFileContent,
    isFileResolved,
    joinLines,
    applyChunkChoice,
    chunkNumber,
    chunkLabel,
  }
}
