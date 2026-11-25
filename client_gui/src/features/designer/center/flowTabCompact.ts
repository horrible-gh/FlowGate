// flowgate.v02test.0088 — the Flow tab strip's 번호만 보기 (a double click on a tab switches the whole strip; again = back).
//
// One state for the strip, not per tab. Kept in this browser's localStorage (the person's view, like the attachments
// list's sort) so a reload keeps it; private mode / full storage just does not remember it. Pure: no Vue.

export const FLOW_TAB_COMPACT_STORAGE_KEY = 'fg2.designer.flowTabs.compact'

/** A Flow without a number shows this many letters of its name in 번호만 보기 (then `…`). */
export const SHORT_TAB_NAME_LENGTH = 6

function storage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

export function readFlowTabCompact(): boolean {
  try { return storage()?.getItem(FLOW_TAB_COMPACT_STORAGE_KEY) === '1' } catch { return false }
}

export function writeFlowTabCompact(compact: boolean): void {
  try {
    if (compact) storage()?.setItem(FLOW_TAB_COMPACT_STORAGE_KEY, '1')
    else storage()?.removeItem(FLOW_TAB_COMPACT_STORAGE_KEY)
  } catch { /* private mode / full storage: the mode is just not remembered */ }
}

/** The first letters of a name (whole characters, not UTF-16 halves), with `…` when it was longer. */
export function shortTabName(name: string, length = SHORT_TAB_NAME_LENGTH): string {
  const letters = Array.from(name.trim())
  return letters.length > length ? `${letters.slice(0, length).join('').trimEnd()}…` : letters.join('')
}
