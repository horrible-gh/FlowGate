// flowgate.v02.0036 — tab list of the bottom Inspector (0035 v7): 문서 · Q&A · 첨부 · 설정, in
// this fixed order for every Node. The first three are always there; only the trailing 설정 tab
// may be missing for a Node that has nothing to set, so the tabs before it never move. Which
// Nodes have settings comes with the card definition in a later group — until then the parent
// decides (DesignerInspector `has-settings`, default true).

export const INSPECTOR_TABS = ['documents', 'qna', 'attachments', 'settings'] as const

export type InspectorTabKey = (typeof INSPECTOR_TABS)[number]

/** The tab a hidden selection falls back to (v7: leaving a Node's 설정 tab returns to 문서). */
export const DEFAULT_INSPECTOR_TAB: InspectorTabKey = 'documents'

/** Tabs shown for one Node, in INSPECTOR_TABS order. */
export function inspectorTabsFor(hasSettings: boolean): readonly InspectorTabKey[] {
  return hasSettings ? INSPECTOR_TABS : INSPECTOR_TABS.filter((key) => key !== 'settings')
}

/**
 * The selected tab is kept while the user moves between Nodes (v7); when the new Node does
 * not have it, the selection goes back to 문서.
 */
export function resolveInspectorTab(current: InspectorTabKey, available: readonly InspectorTabKey[]): InspectorTabKey {
  return available.includes(current) ? current : DEFAULT_INSPECTOR_TAB
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
