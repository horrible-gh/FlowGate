// How the attachment window orders, filters and range-selects its rows (0074 · items 3 and 4). Pure helpers: the
// window keeps the state, these only answer "in which order", "which files does this chip keep" and "which keys does
// a Shift+click cover".
import type { FlowFile } from './api'

/** Row order. Newest first is the default: a file just uploaded or just approved is on top. */
export type SortKey = 'newest' | 'oldest' | 'name'
export const SORT_KEYS: readonly SortKey[] = ['newest', 'oldest', 'name']
/** Origin chips. `ai` = files an AI request put here after approval, `upload` = files a person added directly. */
export type OriginFilter = 'all' | 'ai' | 'upload'
export const ORIGIN_FILTERS: readonly OriginFilter[] = ['all', 'ai', 'upload']

/** The upload / approval time in ms; a missing or broken time sorts after every dated file. */
export function fileTime(file: Pick<FlowFile, 'uploaded_at'>): number | null {
  if (!file.uploaded_at) return null
  const at = Date.parse(file.uploaded_at)
  return Number.isNaN(at) ? null : at
}

const byName = (a: FlowFile, b: FlowFile) => a.display_name.localeCompare(b.display_name, undefined, { numeric: true, sensitivity: 'base' })

/** A sorted copy. Ties keep the order they came in (the server's), so equal times never shuffle. */
export function sortFiles<T extends FlowFile>(files: readonly T[], key: SortKey): T[] {
  const indexed = files.map((file, index) => ({ file, index, at: fileTime(file) }))
  indexed.sort((a, b) => {
    let order = 0
    if (key === 'name') order = byName(a.file, b.file)
    else if (a.at === null || b.at === null) order = a.at === b.at ? 0 : a.at === null ? 1 : -1
    else order = key === 'newest' ? b.at - a.at : a.at - b.at
    return order || a.index - b.index
  })
  return indexed.map(x => x.file)
}

export function matchesOrigin(file: Pick<FlowFile, 'origin'>, filter: OriginFilter): boolean {
  return filter === 'all' || file.origin === filter
}

/** Chip counts over the files given (the window passes the search result, so counts follow the search). */
export function countByOrigin(files: readonly Pick<FlowFile, 'origin'>[]): Record<OriginFilter, number> {
  const counts: Record<OriginFilter, number> = { all: files.length, ai: 0, upload: 0 }
  for (const file of files) {
    if (file.origin === 'ai') counts.ai++
    else if (file.origin === 'upload') counts.upload++
  }
  return counts
}

/**
 * The keys a Shift+click covers: from the anchor row to the clicked row, both included, in the order shown.
 * Null when the anchor is not among the shown rows (filtered or searched away): the click is then a plain toggle.
 */
export function rangeKeys(order: readonly string[], anchor: string | null, target: string): string[] | null {
  if (anchor === null) return null
  const from = order.indexOf(anchor), to = order.indexOf(target)
  if (from < 0 || to < 0) return null
  const [start, end] = from <= to ? [from, to] : [to, from]
  return order.slice(start, end + 1)
}

/* ── the chosen order is remembered in this browser (mockup 3: "고른 정렬은 기억합니다") ── */
export const SORT_STORAGE_KEY = 'fg2.flowAttachments.sort'
function storage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}
export function readSort(): SortKey {
  try {
    const value = storage()?.getItem(SORT_STORAGE_KEY)
    return SORT_KEYS.includes(value as SortKey) ? value as SortKey : 'newest'
  } catch { return 'newest' }
}
export function writeSort(key: SortKey): void {
  try { storage()?.setItem(SORT_STORAGE_KEY, key) } catch { /* private mode / full storage: the order is just not remembered */ }
}
