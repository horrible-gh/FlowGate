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
// muted text color.
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
    accent: '#8b5cf6',
  },
  memo: { icon: 'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5', accent: '#9aa8ba' },
  unknown: { icon: 'M5 5h14v14H5z', accent: '#7f95ad' },
}

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

export function flowNodeKindDisplay(kind: string): FlowNodeKindDisplay {
  const key: FlowNodeKindKey = isKnown(FLOW_NODE_KINDS, kind) ? kind : 'unknown'
  return { key, ...KIND_TABLE[key] }
}

/** null when the Node has no state or an unknown state key (the state is then omitted). */
export function flowNodeStateDisplay(state: string | null | undefined): FlowNodeStateDisplay | null {
  if (!state || !isKnown(FLOW_NODE_STATES, state)) return null
  return { key: state, tone: STATE_TONES[state] }
}
