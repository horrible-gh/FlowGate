// Inspector tabs use a fixed order: documents, settings, AI, Q&A.
// The Frame shows AI only when the selected card contributes it.
// Attachments are managed in the flow attachment window.

export const INSPECTOR_TABS = ['documents', 'settings', 'ai', 'qna'] as const

export type InspectorTabKey = (typeof INSPECTOR_TABS)[number]

/** The tab selected first (v7: 문서). */
export const DEFAULT_INSPECTOR_TAB: InspectorTabKey = 'documents'

/** Tabs shown for one Node: those with a contribution, in INSPECTOR_TABS order. */
export function inspectorTabsWith(contributed: Iterable<string>): readonly InspectorTabKey[] {
  const keys = new Set(contributed)
  return INSPECTOR_TABS.filter((key) => keys.has(key))
}

/**
 * The selected tab is kept while the user moves between Nodes (v7); when the new Node does
 * not have it, the selection goes to the first shown tab (usually 문서). With no tab shown the
 * selection is left as it is.
 */
export function resolveInspectorTab(current: InspectorTabKey, available: readonly InspectorTabKey[]): InspectorTabKey {
  return available.includes(current) || available.length === 0 ? current : available[0]
}

// Tab strip geometry. The strip never scrolls or wraps: when the Inspector is narrow (the
// 360px Center minimum) each tab shrinks down to INSPECTOR_TAB_MIN_WIDTH and ellipsises its
// label, so every tab stays on screen and clickable. The Shell sizes the Inspector track to at
// least inspectorTabStripMinWidth(all tabs).

/** Narrowest a tab may shrink to — still wider than a two-letter ko label plus padding. */
export const INSPECTOR_TAB_MIN_WIDTH = 52
export const INSPECTOR_TAB_GAP = 3
export const INSPECTOR_TAB_STRIP_PADDING_X = 10

/** Width the tab strip needs to show `count` tabs at their minimum width. */
export function inspectorTabStripMinWidth(count: number): number {
  return count * INSPECTOR_TAB_MIN_WIDTH + Math.max(count - 1, 0) * INSPECTOR_TAB_GAP + 2 * INSPECTOR_TAB_STRIP_PADDING_X
}
