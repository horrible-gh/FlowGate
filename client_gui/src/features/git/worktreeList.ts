// flowgate.v02.0066 — what the Git tab's changed-files accordion draws, computed from one E1
// answer (0066 D#2 §3-6). Pure: no Vue, no server. The worktree session keeps the inputs (search,
// filter, folder state, display limits, selection) and the components draw the result.
//
// Only the server's row values decide anything: a row's section comes from its letters, what it
// may do from its `can*` flags. Hidden entries are never listed — they exist here only as the
// count of a Stage the list does not show (hiddenStagedCount).
import type { WorktreeFile, WorktreeStatus } from './types'

/** Rows a folder shows at first and adds per `더 보기` (v19 mock-up: 10 at a time). */
export const WORKTREE_FOLDER_PAGE = 10

export type WorktreeSectionKey = 'changes' | 'staged'

/** Status filter of the search row: all / modified / added / new file / deleted / not operable. */
export type WorktreeFilter = 'all' | 'M' | 'A' | '?' | 'D' | 'locked'
export const WORKTREE_FILTERS: readonly WorktreeFilter[] = ['all', 'M', 'A', '?', 'D', 'locked']

/** default = only the first folder open; open / closed = the file menu's 모든 폴더 열기 / 접기. */
export type WorktreeFolderMode = 'default' | 'open' | 'closed'

/** What a row shows in one section: its file and the letter of that section. */
export interface WorktreeListRow {
  file: WorktreeFile
  section: WorktreeSectionKey
  // changes: the working letter (M / D / T) or `?` for an untracked file; staged: the index letter.
  letter: string
}

export interface WorktreeFolderView {
  // `${section}|${folder}`: the display limit is kept per section.
  key: string
  // Folded / unfolded is kept per folder path, shared by both sections (as the v19 mock-up).
  folder: string
  // Rows of this folder matching the search and filter.
  count: number
  open: boolean
  rows: WorktreeListRow[]
  // Matching rows past the display limit.
  remaining: number
  // flowgate.v02.0067 T#2 — every matching row of this folder that may be checked, folded or past
  // the display limit included: what the folder checkbox selects and reads its state from.
  selectable: string[]
}

/** The folder checkbox: all / some / none of its selectable rows checked (0067-R §4-2). */
export type WorktreeFolderSelection = 'all' | 'some' | 'none'

export interface WorktreeSectionView {
  key: WorktreeSectionKey
  // Every row of the section / the ones matching the search and filter.
  total: number
  matched: number
  folders: WorktreeFolderView[]
  // Staged section only: the Stage no listed row carries (0066 D#2 §3-6(0)), else 0.
  hiddenStaged: number
}

export interface WorktreeListOptions {
  search: string
  filter: WorktreeFilter
  folderMode: WorktreeFolderMode
  // Explicit folder toggles by folder path; they win over the mode.
  folderToggles: Readonly<Record<string, boolean>>
  // Display limit by folder key (absent = one page).
  limits: Readonly<Record<string, number>>
}

export const inChanges = (file: WorktreeFile): boolean => !!file.unstaged || file.untracked
export const inStaged = (file: WorktreeFile): boolean => !!file.staged

export function rowLetter(file: WorktreeFile, section: WorktreeSectionKey): string {
  if (section === 'staged') return file.staged ?? ''
  return file.untracked ? '?' : file.unstaged ?? ''
}

/** A protected or unsupported row: no Stage / revert / remove of its own (§3-6(4)). */
export function isLocked(file: WorktreeFile): boolean {
  return !!file.unsupported || !!file.protectedReason || file.fileClass === 'managed' || file.fileClass === 'unverified'
}

/**
 * A row can be checked only for what it may do: Stage, or release its Stage. A FlowGate-managed,
 * protected or unsupported row is never checked (0067-R §4-3): its own `−` (when the server lets
 * it) and the full unstage stay its way out.
 */
export const isSelectable = (file: WorktreeFile): boolean => (file.canStage || file.canUnstage) && !isLocked(file)

/** `counts.staged` (hidden included) minus the listed staged rows; never below 0 (§3-6(0)). */
export function hiddenStagedCount(status: WorktreeStatus | null): number {
  if (!status) return 0
  const listed = status.files.filter(inStaged).length
  return Math.max(0, (status.counts.staged ?? 0) - listed)
}

/** Listed staged rows whose Stage the server will not commit (§3-5 rank 10). */
export function stagedProtectedRows(status: WorktreeStatus | null): number {
  if (!status) return 0
  return status.files.filter(
    (f) => inStaged(f) && (f.fileClass === 'managed' || f.protectedReason === 'excluded_new' || !!f.unsupported),
  ).length
}

/** Nothing at all to show: every count the header reads is 0 (or unknown). */
export function isWorktreeEmpty(status: WorktreeStatus | null): boolean {
  if (!status) return true
  const c = status.counts
  return !c.changed && !c.staged && !c.hidden && !c.excludedNewUntracked && status.files.length === 0
}

export function folderOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut < 0 ? '' : path.slice(0, cut)
}

export function fileNameOf(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

export function matchesRow(row: WorktreeListRow, search: string, filter: WorktreeFilter): boolean {
  const needle = search.trim().toLowerCase()
  if (needle && !row.file.path.toLowerCase().includes(needle)) return false
  if (filter === 'all') return true
  if (filter === 'locked') return isLocked(row.file)
  return row.letter === filter
}

function rowsOf(files: readonly WorktreeFile[], section: WorktreeSectionKey): WorktreeListRow[] {
  return files
    .filter(section === 'staged' ? inStaged : inChanges)
    .map((file) => ({ file, section, letter: rowLetter(file, section) }))
}

function groupByFolder(rows: WorktreeListRow[]): Map<string, WorktreeListRow[]> {
  const groups = new Map<string, WorktreeListRow[]>()
  for (const row of rows) {
    const folder = folderOf(row.file.path)
    const list = groups.get(folder)
    if (list) list.push(row)
    else groups.set(folder, [row])
  }
  return new Map([...groups.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
}

/**
 * The two sections of the open accordion. A partly staged file is in both. Folders are sorted by
 * name and rows by path; with a search every matching folder is open, otherwise the explicit
 * toggle of that folder path, else the mode (default: only the first folder of the list is open,
 * in whichever section it appears).
 */
export function buildSections(status: WorktreeStatus | null, options: WorktreeListOptions): WorktreeSectionView[] {
  if (!status) return []
  const searching = !!options.search.trim()
  const hidden = hiddenStagedCount(status)
  let firstFolder: string | null = null
  const sections: WorktreeSectionView[] = []
  for (const key of ['changes', 'staged'] as const) {
    const all = rowsOf(status.files, key)
    if (key === 'staged' && !all.length && !hidden) continue
    if (key === 'changes' && !all.length) continue
    const matched = all.filter((row) => matchesRow(row, options.search, options.filter))
    const folders: WorktreeFolderView[] = []
    for (const [folder, rows] of groupByFolder(matched)) {
      rows.sort((a, b) => (a.file.path < b.file.path ? -1 : a.file.path > b.file.path ? 1 : 0))
      const folderKey = `${key}|${folder}`
      firstFolder ??= folder
      const toggled = options.folderToggles[folder]
      const open = searching
        ? true
        : toggled !== undefined
          ? toggled
          : options.folderMode === 'open' || (options.folderMode === 'default' && folder === firstFolder)
      const limit = Math.max(WORKTREE_FOLDER_PAGE, options.limits[folderKey] ?? WORKTREE_FOLDER_PAGE)
      const shown = open ? rows.slice(0, limit) : []
      folders.push({
        key: folderKey,
        folder,
        count: rows.length,
        open,
        rows: shown,
        remaining: open ? rows.length - shown.length : 0,
        selectable: rows.filter((row) => isSelectable(row.file)).map((row) => row.file.path),
      })
    }
    sections.push({ key, total: all.length, matched: matched.length, folders, hiddenStaged: key === 'staged' ? hidden : 0 })
  }
  return sections
}

/** Paths `전체 선택` checks: selectable rows that match the search and filter, both sections. */
export function selectAllTargets(status: WorktreeStatus | null, search: string, filter: WorktreeFilter): string[] {
  if (!status) return []
  const out = new Set<string>()
  for (const key of ['changes', 'staged'] as const) {
    for (const row of rowsOf(status.files, key)) {
      if (isSelectable(row.file) && matchesRow(row, search, filter)) out.add(row.file.path)
    }
  }
  return [...out]
}

/** The folder checkbox state of `paths` (the folder's selectable rows) against the selection. */
export function folderSelection(paths: readonly string[], selected: readonly string[]): WorktreeFolderSelection {
  if (!paths.length) return 'none'
  const set = new Set(selected)
  const n = paths.filter((p) => set.has(p)).length
  return n === 0 ? 'none' : n === paths.length ? 'all' : 'some'
}

/** After a status change: only paths still listed and still selectable stay selected (§3-6(4)). */
export function pruneSelection(selected: readonly string[], status: WorktreeStatus | null): string[] {
  if (!status) return []
  const keep = new Set(status.files.filter(isSelectable).map((f) => f.path))
  return selected.filter((path) => keep.has(path))
}

/** `+ 선택 Stage` sends only the selected rows the server lets stage. */
export function stageTargets(selected: readonly string[], status: WorktreeStatus | null): string[] {
  if (!status) return []
  const set = new Set(selected)
  return status.files.filter((f) => set.has(f.path) && f.canStage).map((f) => f.path)
}

/** `− 선택 해제` sends only the selected rows that are staged and may be released. */
export function unstageTargets(selected: readonly string[], status: WorktreeStatus | null): string[] {
  if (!status) return []
  const set = new Set(selected)
  return status.files.filter((f) => set.has(f.path) && inStaged(f) && f.canUnstage).map((f) => f.path)
}

// ── `Diff` list (flowgate.v02.0067 T#4, v27 `gw-diff-search` / `gw-diff-pages`) ─────────────────────

/** Rows one page of the `Diff` tab's comparison list shows (v27: 16). */
export const WORKTREE_DIFF_PAGE = 16

export interface WorktreeDiffPage<T> {
  rows: T[]
  // Rows matching the search, over every page.
  matched: number
  // The page shown (0-based, kept inside the pages there are) and how many there are (at least 1).
  page: number
  pages: number
  // 1-based numbers of the first and last row shown; 0 / 0 when nothing matches.
  from: number
  to: number
}

/** One page of the comparison list: rows whose path contains the search (any case), in list order. */
export function diffListPage<T extends { path: string }>(
  changes: readonly T[],
  search: string,
  page: number,
  size = WORKTREE_DIFF_PAGE,
): WorktreeDiffPage<T> {
  const needle = search.trim().toLowerCase()
  const matches = needle ? changes.filter((c) => c.path.toLowerCase().includes(needle)) : changes
  const pages = Math.max(1, Math.ceil(matches.length / size))
  const at = Math.min(Math.max(0, Math.floor(page)), pages - 1)
  const rows = matches.slice(at * size, (at + 1) * size)
  return { rows, matched: matches.length, page: at, pages, from: rows.length ? at * size + 1 : 0, to: at * size + rows.length }
}
