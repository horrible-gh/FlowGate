// flowgate.v02.0036 — tab list of the bottom Inspector (0035 v7): 문서 · Q&A · 첨부 · 설정, in
// this fixed order for every Node.
// flowgate.v02.0048 T#1 (D#1 §5.3): a tab is shown only when a registered feature contributes to
// it for the selected Node — no empty tab and no placeholder. The order never changes; which tabs
// exist comes from the registry, never from a per-Node flag.

export const INSPECTOR_TABS = ['documents', 'qna', 'attachments', 'settings'] as const

export type InspectorTabKey = (typeof INSPECTOR_TABS)[number]

/** The tab selected first (v7: 문서). */
export const DEFAULT_INSPECTOR_TAB: InspectorTabKey = 'documents'

/** Tabs shown for one Node: those with a contribution, in INSPECTOR_TABS order. */
export function inspectorTabsWith(contributed: Iterable<string>): readonly InspectorTabKey[] {
  const keys = new Set(contributed)
  return INSPECTOR_TABS.filter((key) => keys.has(key))
}

/** Card kinds whose v0.1 sequence step carries run settings (the settings feature's cards). */
const RUN_SETTINGS_CARD_KINDS: readonly string[] = ['research', 'task', 'design', 'test']

/**
 * flowgate.v02.0052 T#2 — why the 설정 tab of a Node has no part (no registration contributes), as a
 * message key under `gui.designer.inspector.pane.`: a run card outside a v0.1 sequence step says which
 * fact keeps the run settings away (Graph Flow · ownership conflict · undecided Flow · no sequence slot);
 * any other card kind is no run settings target (the registry gives it none).
 */
export function inspectorNoSettingsKey(target: { readonly cardKind: string; readonly ownership: string | null; readonly slot: unknown }): string {
  if (!RUN_SETTINGS_CARD_KINDS.includes(target.cardKind)) return 'no_settings'
  if (target.ownership === 'graph') return 'no_settings_for.graph'
  if (target.ownership === 'conflict') return 'no_settings_for.conflict'
  if (target.ownership !== 'v01') return 'no_settings_for.unassigned'
  return target.slot ? 'no_settings' : 'no_settings_for.no_slot'
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
