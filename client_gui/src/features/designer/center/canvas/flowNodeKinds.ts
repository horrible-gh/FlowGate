// flowgate.v02.0026 — display table of Node kind / state keys (0003-D §4.1.5 / §6.3): the
// i18n key, glyph and accent of each known key. Keys are open: anything not listed renders as
// the default 'unknown' kind (neutral accent), and an unknown state shows no state at all —
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
  /** SVG path data on a 16x16 box, drawn with stroke="currentColor". */
  icon: string
  /** CSS color built only from --fg2-* tokens. */
  accent: string
}

export interface FlowNodeStateDisplay {
  key: FlowNodeStateKey
  tone: FlowNodeStateTone
}

const KIND_TABLE: Record<FlowNodeKindKey, Omit<FlowNodeKindDisplay, 'key'>> = {
  requirement: { icon: 'M4 2.5h6l2.5 2.5v8.5h-8.5z M6.5 7.5h4 M6.5 10h4', accent: 'var(--fg2-blue)' },
  research: { icon: 'M7 3a4 4 0 1 1 0 8a4 4 0 1 1 0-8z M10 10l3.5 3.5', accent: 'var(--fg2-cyan)' },
  plan: { icon: 'M3 4h10 M3 8h7 M3 12h4', accent: 'var(--fg2-purple)' },
  task: { icon: 'M3 3h10v10h-10z M5.5 8l2 2l3.5-4', accent: 'var(--fg2-orange)' },
  test: { icon: 'M6 2.5h4 M7 2.5v4l-3.5 6.5h9l-3.5-6.5v-4', accent: 'var(--fg2-yellow)' },
  approval: {
    icon: 'M8 2.5a5.5 5.5 0 1 1 0 11a5.5 5.5 0 1 1 0-11z M5.5 8l2 2l3-3.5',
    accent: 'var(--fg2-status-success-fg)',
  },
  design: { icon: 'M2.5 13.5l2-5l6-6l3 3l-6 6z M9 4l3 3', accent: 'var(--fg2-violet)' },
  memo: { icon: 'M3 3h10v7l-3 3h-7z M10 13v-3h3', accent: 'var(--fg2-muted)' },
  unknown: { icon: 'M3 3h10v10h-10z', accent: 'var(--fg2-muted-2)' },
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
