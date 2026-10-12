// flowgate.v02.0056 F1 — what a slot line shows for its state, decided in one place for every card (0056 CH
// §1 / §5). The 0053 line drew every state as a coloured dot whose meaning lived in its tooltip; since most
// lines sit in a state that asks nothing of the operator ('작성 중', '완료'), the colour carried no
// information. Now a line carries a short text badge only when the state says something: a decision waits
// (warning / danger), something runs or holds (running / hold), the document was revised (info), or it is
// settled (success — drawn dim). A neutral state draws nothing at all. A line without a document says why
// in plain words (the former hollow dot's tooltip) where the badge would be.
// Pure: no Vue, no i18n — the caller hands the worded label, this decides whether and how it is drawn.

export type SlotStateTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface SlotStateInput {
  readonly label: string
  readonly tone: SlotStateTone
  /** More hover text after the label (a reason, "2/4"). */
  readonly sub?: string | null
  readonly title?: string
}

export interface SlotStateView {
  /** badge: a text badge; empty: why the line has no document; none: nothing is drawn. */
  readonly kind: 'badge' | 'empty' | 'none'
  readonly tone: SlotStateTone | 'none'
  /** The text drawn ('' for none). */
  readonly text: string
  /** Hover text: the label with its sub text and title (what the 0053 dot tooltip said). */
  readonly title: string | undefined
  /** The badge says something is settled (success): drawn dim so a waiting badge stands out. */
  readonly quiet: boolean
}

const NONE: SlotStateView = { kind: 'none', tone: 'none', text: '', title: undefined, quiet: false }

/** Tones that say something to the operator; `neutral` is silence. */
export const SLOT_STATE_SHOWN_TONES: readonly SlotStateTone[] = ['info', 'success', 'warning', 'danger', 'running', 'hold']

/** The hover text of a state: its label, then its sub text and title when given. */
export function slotStateTitle(state: SlotStateInput): string {
  const more = [state.sub, state.title].filter((text): text is string => !!text)
  return [state.label, ...more].join(' · ')
}

/**
 * What the line draws for its state. `empty` wins: a line without a document shows `emptyText` (the common
 * wording when the caller gives none) as plain muted text. A state of a shown tone is a badge; `neutral`
 * and no state draw nothing.
 */
export function slotStateView(state: SlotStateInput | null | undefined, empty: boolean, emptyText: string | null | undefined): SlotStateView {
  if (empty) {
    const text = emptyText ?? ''
    return { kind: 'empty', tone: 'none', text, title: text || undefined, quiet: false }
  }
  if (!state || !SLOT_STATE_SHOWN_TONES.includes(state.tone)) return NONE
  return { kind: 'badge', tone: state.tone, text: state.label, title: slotStateTitle(state), quiet: state.tone === 'success' }
}
