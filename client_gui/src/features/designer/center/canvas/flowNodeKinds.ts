// flowgate.v02.0026 — display table of Node kind / state keys (0003-D §4.1.5 / §6.3): the
// i18n key, glyph and accent of each known key. Keys are open: anything not listed renders as
// the default 'unknown' kind (muted accent), and an unknown state shows no state at all —
// a new key from a future server never breaks the Canvas. The key lists must match
// designer.center.node_kinds / node_states in ko / en / ja (structure.0026).

export const FLOW_NODE_KINDS = [
  'requirement',
  'research',
  'plan',
  'task',
  'test',
  'approval',
  'design',
  'memo',
] as const

export const FLOW_NODE_STATES = ['draft', 'in_progress', 'review', 'done', 'blocked'] as const

export type FlowNodeKindKey = (typeof FLOW_NODE_KINDS)[number] | 'unknown'
export type FlowNodeStateKey = (typeof FLOW_NODE_STATES)[number]
export type FlowNodeStateTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export interface FlowNodeKindDisplay {
  key: FlowNodeKindKey
  /** SVG path data on a 24x24 box, drawn with stroke="currentColor" (03-designer-shell glyphs). */
  icon: string
  /** Kind color of the card frame, head row and icon box (03-designer-shell `--c`). */
  accent: string
}

export interface FlowNodeStateDisplay {
  key: FlowNodeStateKey
  tone: FlowNodeStateTone
}

// Colors and glyphs follow 03-designer-shell. design / memo / unknown are not on its Canvas:
// design takes the deck palette's purple, memo the deck's memo palette card, unknown the deck's
// muted text color. flowgate.v02.0045 T0019: every 설계 card (Canvas and Palette) uses this one
// purple (the theme's --fg2-purple).
const KIND_TABLE: Record<FlowNodeKindKey, Omit<FlowNodeKindDisplay, 'key'>> = {
  requirement: { icon: 'M4 5h16v14H4z M7 9h10 M7 13h7', accent: '#3b82f6' },
  research: { icon: 'M5 10a5 5 0 1 0 10 0a5 5 0 1 0-10 0z M14 14l6 6', accent: '#22c98c' },
  plan: {
    icon: 'M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M9 4V2h6v2 M8 9h8 M8 13h8 M8 17h5',
    accent: '#15c9d4',
  },
  task: { icon: 'M14 6l4 4l-8 8H6v-4l8-8z M12 8l4 4', accent: '#f59e0b' },
  test: { icon: 'M9 3h6 M10 3v6l-5 9h14l-5-9V3 M8 15h8', accent: '#ec4899' },
  approval: { icon: 'M12 3l8 4v5c0 5-3.5 8-8 9c-4.5-1-8-4-8-9V7z M8.5 12l2.2 2.2l4.8-5', accent: '#22c98c' },
  design: {
    icon: 'M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M8 21h8 M12 17v4',
    accent: '#9b72ff',
  },
  memo: { icon: 'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5', accent: '#9aa8ba' },
  unknown: { icon: 'M5 5h14v14H5z', accent: '#7f95ad' },
}

/**
 * flowgate.v02.0061 T#15 — the Hold mark of a held card (R): a stop-sign like glyph, an open palm inside a round frame.
 * Same glyph format as the kinds (24x24 path, stroke="currentColor").
 */
export const FLOW_NODE_HOLD_ICON =
  'M12 2.5a9.5 9.5 0 1 0 0 19a9.5 9.5 0 1 0 0-19z ' +
  'M9.5 11.5V8 M11.5 11V6.8 M13.5 11V7.3 M15.5 12V8.8 ' +
  'M15.5 12v1.8a3.7 3.7 0 0 1-3.7 3.7h-.6a3.4 3.4 0 0 1-2.8-1.5L6.9 13.3a1 1 0 0 1 1.6-1.2l1 1.2V11.5'

const STATE_TONES: Record<FlowNodeStateKey, FlowNodeStateTone> = {
  draft: 'neutral',
  in_progress: 'info',
  review: 'warning',
  done: 'success',
  blocked: 'danger',
}

function isKnown<T extends string>(list: readonly T[], value: string): value is T {
  return (list as readonly string[]).includes(value)
}

/**
 * Display of a Node kind. flowgate.v02.0045 TR0020 rev2: a 설계 Node with a known document type
 * (D / P / L / DB) takes that type's own glyph, so the four 설계 cards are told apart at a glance
 * while they keep the one 설계 purple.
 */
export function flowNodeKindDisplay(kind: string, docType?: string | null): FlowNodeKindDisplay {
  const key: FlowNodeKindKey = isKnown(FLOW_NODE_KINDS, kind) ? kind : 'unknown'
  const designIcon = key === 'design' ? DESIGN_DOC_TYPE_ICONS[designDocType(docType)] : undefined
  return { key, ...KIND_TABLE[key], ...(designIcon ? { icon: designIcon } : {}) }
}

/**
 * flowgate.v02.0045 T0019: the four 설계 document types share the 'design' kind; on the Canvas
 * each is named like its Palette card (gui.designer.palette.items.<key>), not just "설계".
 */
export const DESIGN_DOC_TYPE_LABELS: Readonly<Record<string, string>> = {
  D: 'basic_design',
  P: 'protocol',
  L: 'logic',
  DB: 'db_design',
}

/**
 * flowgate.v02.0045 TR0020 rev2: the glyph of each 설계 document type — the same drawings as its
 * Palette card (palette/paletteIcons.ts monitor / flow / logic / database; the Canvas does not
 * import the Palette, D0004 §4.2). Other 설계 types (DS, none) keep the 'design' glyph.
 */
export const DESIGN_DOC_TYPE_ICONS: Readonly<Record<string, string>> = {
  D: 'M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-9a2 2 0 0 1 2 -2z M8 21h8M12 17v4',
  P: 'M4 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M16 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M10 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M8 6h8M7.5 7.5 11 16M16.5 7.5 13 16',
  L: 'M4 5h6v5H4zM14 14h6v5h-6zM14 3h6v5h-6zM10 7h2v9h2',
  DB: 'M5 5a7 3 0 1 0 14 0a7 3 0 1 0 -14 0z M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7',
}

const designDocType = (docType?: string | null) => (docType ? docType.trim().toUpperCase() : '')

/** i18n key of the name a Node card shows for its kind (and document type). */
export function flowNodeKindLabelKey(kind: string, docType?: string | null): string {
  const key = flowNodeKindDisplay(kind).key
  const designLabel = key === 'design' ? DESIGN_DOC_TYPE_LABELS[designDocType(docType)] : undefined
  return designLabel ? `gui.designer.palette.items.${designLabel}` : `gui.designer.center.node_kinds.${key}`
}

/** null when the Node has no state or an unknown state key (the state is then omitted). */
export function flowNodeStateDisplay(state: string | null | undefined): FlowNodeStateDisplay | null {
  if (!state || !isKnown(FLOW_NODE_STATES, state)) return null
  return { key: state, tone: STATE_TONES[state] }
}
