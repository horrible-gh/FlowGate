// flowgate.v02.0060 T#2 (WP 0012, 0006-D rev2 §4.3 2-B) — what the Designer context menu's 11 existing server
// commands need besides the menu: the request a chosen item becomes (its target as the menu saw it), the server paths
// the Designer Shell lends (each the existing contract through features/project — no new API, no store here), the
// answer shape the dialogs read, and two pure helpers (the name rule the server applies, the line diff of the
// 수정 확인 window). Flow 삭제 is the existing dispose (DC); nothing here deletes a Flow, a document or a branch.

/** The 2-B commands and the dialog each opens (파일 다운로드 runs at once: no dialog). */
export type MenuActionKind =
  | 'group.rename' | 'flow.rename' | 'flow.delete'
  | 'folder.newFolder' | 'folder.newFile' | 'folder.download' | 'folder.upload' | 'folder.delete'
  | 'file.download' | 'file.git.changes' | 'file.delete'

export type MenuActionTarget =
  | { readonly kind: 'group.rename'; readonly projectId: string; readonly groupId: string }
  | { readonly kind: 'flow.rename' | 'flow.delete'; readonly projectId: string; readonly groupId: string; readonly flowId: string }
  | {
    readonly kind: 'folder.newFolder' | 'folder.newFile' | 'folder.download' | 'folder.upload' | 'folder.delete'
    readonly projectId: string
    readonly flowId: string
    /** '' = the root folder (the File Explorer's blank area). */
    readonly path: string
    readonly root: boolean
  }
  | { readonly kind: 'file.download' | 'file.git.changes' | 'file.delete'; readonly projectId: string; readonly flowId: string; readonly path: string }

/** Why a command did not happen: the server's refusal (features/project) or what the window found first. */
export type MenuActionRefusalReason =
  | 'disposed' | 'not_found' | 'forbidden' | 'locked' | 'managed' | 'exists' | 'invalid' | 'worktree' | 'too_large'
  | 'network' | 'failed' | 'base_unavailable' | 'base_mismatch'
export interface MenuActionRefusal {
  readonly reason: MenuActionRefusalReason
  readonly status: number | null
  readonly code: string | null
}
export type MenuActionResult<T = void> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly refusal: MenuActionRefusal }

export interface MenuActionFile { readonly filename: string; readonly blob: Blob }
export interface MenuActionUpload {
  readonly uploaded: readonly { readonly path: string; readonly size: number }[]
  readonly skipped: readonly { readonly path: string; readonly reason: string }[]
}
export interface MenuActionDiffSide {
  readonly exists: boolean
  readonly binary: boolean
  readonly truncated: boolean
  readonly content: string | null
}
export interface MenuActionDiff {
  /** The project Base the diff was asked against and answered for. */
  readonly base: string
  readonly status: 'M' | 'A' | 'D'
  readonly old: MenuActionDiffSide
  readonly new: MenuActionDiffSide
}

/**
 * The existing server paths the Designer Shell lends (features/project store → the v0.1 contracts). Each answers a
 * result, never throws; on success the Shell has already refreshed what shows the change (Project tree, recent
 * lists, tab names, the File Explorer tree, a disposed Flow's tab).
 */
export interface DesignerMenuServerDeps {
  /** The Group as the Project Explorer shows it now (display name, module key); null once gone. */
  readonly group: (projectId: string, groupId: string) => { readonly name: string; readonly moduleName: string } | null
  /** The Flow's display name as the Project Explorer shows it now; null once gone. */
  readonly flow: (projectId: string, flowId: string) => string | null
  readonly renameGroup: (projectId: string, groupId: string, title: string) => Promise<MenuActionResult>
  readonly renameFlow: (projectId: string, groupId: string, flowId: string, title: string) => Promise<MenuActionResult>
  /** The existing dispose (DC) with its required reason. */
  readonly disposeFlow: (projectId: string, groupId: string, flowId: string, reason: string) => Promise<MenuActionResult>
  readonly createEntry: (projectId: string, flowId: string, kind: 'folder' | 'file', parentPath: string, name: string) => Promise<MenuActionResult>
  readonly download: (projectId: string, flowId: string, kind: 'folder' | 'file', path: string, fallbackName: string) => Promise<MenuActionResult<MenuActionFile>>
  /**
   * The file names directly under `path` that the Flow's tree read shows now. Only a hint for the overwrite check: the
   * read leaves out ignored and unreadable files, so a name missing here may still be overwritten.
   */
  readonly fileNames: (projectId: string, flowId: string, path: string) => Promise<MenuActionResult<readonly string[]>>
  readonly upload: (projectId: string, flowId: string, targetPath: string, files: readonly File[]) => Promise<MenuActionResult<MenuActionUpload>>
  readonly remove: (projectId: string, flowId: string, kind: 'folder' | 'file', path: string) => Promise<MenuActionResult>
  /** One file of the Flow's worktree against the project Base (read from the project's Git status, named in the diff). */
  readonly fileDiff: (projectId: string, flowId: string, path: string) => Promise<MenuActionResult<MenuActionDiff>>
  /** A short note outside a window (파일 다운로드 runs without one). */
  readonly notify?: (message: string, tone: 'success' | 'warning' | 'danger') => void
}

/** The dialog the host shows: the request and the re-check of the menu that made it. */
export interface PendingMenuAction {
  readonly seq: number
  readonly target: MenuActionTarget
  /** True while the target is still there in the same scope and its item still enabled (the menu built again now). */
  readonly stillValid: () => boolean
}

// Same rule as the server's storage create (process_service._storage_create_target).
const INVALID_NAME = /[/\\:*?"<>|]/
export type EntryNameProblem = 'empty' | 'chars' | 'dots' | null
export function entryNameProblem(name: string): EntryNameProblem {
  const trimmed = name.trim()
  if (!trimmed) return 'empty'
  if (INVALID_NAME.test(trimmed)) return 'chars'
  if (trimmed === '.' || trimmed === '..') return 'dots'
  return null
}

/** The last path segment ('' = the root). */
export const baseName = (path: string) => path.split('/').filter(Boolean).pop() ?? ''
export const joinPath = (parent: string, name: string) => (parent ? `${parent}/${name}` : name)

/** How a line ends: '\n', '\r\n', or nothing (the last line of a file without a final newline). */
export type DiffLineEnd = 'lf' | 'crlf' | 'none'

export interface DiffLine {
  readonly kind: 'context' | 'add' | 'del'
  readonly oldNo: number | null
  readonly newNo: number | null
  readonly text: string
  readonly end: DiffLineEnd
}

interface SplitLine {
  readonly text: string
  readonly end: DiffLineEnd
  /** Text and line end: two lines are the same only when both are (as Git compares them). */
  readonly key: string
}

/** The lines of a text with how each ends, so a final newline added or removed (or CRLF ↔ LF) is a change. */
const splitLines = (text: string | null): SplitLine[] => {
  if (!text) return []
  const out: SplitLine[] = []
  let from = 0
  while (from < text.length) {
    const at = text.indexOf('\n', from)
    if (at < 0) {
      out.push({ text: text.slice(from), end: 'none', key: `${text.slice(from)}\u0000none` })
      break
    }
    const crlf = at > from && text[at - 1] === '\r'
    const line = text.slice(from, crlf ? at - 1 : at)
    out.push({ text: line, end: crlf ? 'crlf' : 'lf', key: `${line}\u0000${crlf ? 'crlf' : 'lf'}` })
    from = at + 1
  }
  return out
}

/** Above this many cells the middle is shown as removed then added (no line matching). */
const LCS_LIMIT = 4_000_000

/** The line diff of two texts: common lines as context, the rest removed (old) / added (new), in order. */
export function lineDiff(oldText: string | null, newText: string | null): DiffLine[] {
  const a = splitLines(oldText)
  const b = splitLines(newText)
  let start = 0
  while (start < a.length && start < b.length && a[start].key === b[start].key) start += 1
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1].key === b[endB - 1].key) {
    endA -= 1
    endB -= 1
  }
  const out: DiffLine[] = []
  const line = (kind: DiffLine['kind'], oldNo: number | null, newNo: number | null, from: SplitLine): DiffLine =>
    ({ kind, oldNo, newNo, text: from.text, end: from.end })
  for (let i = 0; i < start; i += 1) out.push(line('context', i + 1, i + 1, a[i]))
  const midA = a.slice(start, endA)
  const midB = b.slice(start, endB)
  const n = midA.length
  const m = midB.length
  if (n * m > LCS_LIMIT) {
    midA.forEach((from, i) => out.push(line('del', start + i + 1, null, from)))
    midB.forEach((from, j) => out.push(line('add', null, start + j + 1, from)))
  } else {
    // LCS lengths from the end, then walk forward.
    const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        table[i][j] = midA[i].key === midB[j].key ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < n || j < m) {
      if (i < n && j < m && midA[i].key === midB[j].key) {
        out.push(line('context', start + i + 1, start + j + 1, midA[i]))
        i += 1
        j += 1
      } else if (i < n && (j >= m || table[i + 1][j] >= table[i][j + 1])) {
        out.push(line('del', start + i + 1, null, midA[i]))
        i += 1
      } else {
        out.push(line('add', null, start + j + 1, midB[j]))
        j += 1
      }
    }
  }
  for (let k = 0; k < a.length - endA; k += 1) out.push(line('context', endA + k + 1, endB + k + 1, a[endA + k]))
  return out
}

/** A run of unchanged lines the 수정 확인 window folds away (its size is shown). */
export interface DiffGap {
  readonly kind: 'gap'
  readonly count: number
}
export type DiffRow = DiffLine | DiffGap

/**
 * The rows the window shows: every changed line with `context` unchanged lines around it; each longer unchanged run
 * becomes one gap row with its size. No change → no rows. So a change after thousands of equal lines is still shown.
 */
export function diffRows(lines: readonly DiffLine[], context = 3): DiffRow[] {
  const keep = new Uint8Array(lines.length)
  lines.forEach((entry, index) => {
    if (entry.kind === 'context') return
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k += 1) keep[k] = 1
  })
  const out: DiffRow[] = []
  let skipped = 0
  lines.forEach((entry, index) => {
    if (keep[index]) {
      if (skipped) out.push({ kind: 'gap', count: skipped })
      skipped = 0
      out.push(entry)
    } else skipped += 1
  })
  if (skipped && out.length) out.push({ kind: 'gap', count: skipped })
  return out
}
