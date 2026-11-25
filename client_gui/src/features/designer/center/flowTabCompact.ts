// flowgate.v02test.0088 — the Flow tab strip's 번호만 보기, per tab: a double click on a tab folds that tab alone to its
// number (again = back); the other tabs do not change.
//
// The folded tabs' flowIds are kept in this browser's localStorage (the person's view, like the attachments list's sort)
// as a JSON array, so a reload keeps them; private mode / full storage / a damaged value just starts with none folded.
// The old strip-wide key (one state for every tab) is no longer read and is removed on load. Pure: no Vue.

export const FLOW_TAB_COMPACT_IDS_STORAGE_KEY = 'fg2.designer.flowTabs.compactIds'

/** The earlier strip-wide 번호만 보기 ('1'). Not read any more; removed when the folded tabs are loaded. */
export const LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY = 'fg2.designer.flowTabs.compact'

/** A Flow without a number shows this many letters of its name in 번호만 보기 (then `…`). */
export const SHORT_TAB_NAME_LENGTH = 6

function storage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

/** The flowIds of the tabs folded to their number (each once, in stored order). Anything unreadable → none. */
export function readCompactFlowIds(): string[] {
  const store = storage()
  if (!store) return []
  try { store.removeItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY) } catch { /* nothing to clean up */ }
  try {
    const raw = store.getItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return [...new Set(parsed.filter((id): id is string => typeof id === 'string' && id.length > 0))]
  } catch {
    return []
  }
}

export function writeCompactFlowIds(flowIds: readonly string[]): void {
  try {
    const ids = [...new Set(flowIds)]
    if (ids.length) storage()?.setItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY, JSON.stringify(ids))
    else storage()?.removeItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY)
  } catch { /* private mode / full storage: the folded tabs are just not remembered */ }
}

/** The first letters of a name (whole characters, not UTF-16 halves), with `…` when it was longer. */
export function shortTabName(name: string, length = SHORT_TAB_NAME_LENGTH): string {
  const letters = Array.from(name.trim())
  return letters.length > length ? `${letters.slice(0, length).join('').trimEnd()}…` : letters.join('')
}
