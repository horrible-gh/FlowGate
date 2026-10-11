// flowgate.v02.0051 stage 2 — the Graph gate Node's own lines (0035 v7 확인 cards: the 확인 결과 result
// line, ✓ 확인 · ✕ 반려 in the header). A gate has no document, so 0048 showed it as a header only; v7
// gives it the result line and the two actions. Their meaning for a gate (중간 승인 / 그룹 종료 / AI
// 위임 마지막 확인) is still under discussion (v7 §5) and the gate confirm / reject API is not registered
// (D#1 §8.4), so the actions are drawn disabled with that reason and only the common shape is drawn.
// `other` card kind Nodes that are not gates (an unknown document type) get the one document line when
// they have a document, else nothing. Pure functions over the target.
// flowgate.v02.0053 T#5 (0053 CH) — a Custom card (palette [+], Graph `custom` node) works like 조사 · 작업: its
// lines are 지시서 · 레포트, the documents the server made when it was placed (T#1 card lines T / TR); the
// 지시서 line is hidden when the card's settings omit the instruction. A document line's name carries no type
// code.
import type { InspectorTarget } from '../../inspectorTarget'

export type GateSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface GateSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: GateSlotTone
}

export interface GateSlot {
  readonly key: 'result' | 'document' | 'instruction' | 'report'
  readonly kind: 'document' | 'result'
  readonly id: string | null
  readonly empty: boolean
  readonly emptyKey: string | null
  readonly state: GateSlotState | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
}

export type GateCardShape = 'gate' | 'custom' | 'document' | 'none'

export type GateCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus' | 'nodeType' | 'cardDocuments'>

const REVIEW_TONES: Readonly<Record<string, GateSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The pill of a bound document: its review status, else its document status (draft / open / closed). */
export function gateDocumentState(reviewStatus: string | null, docStatus: string | null): GateSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  return { key: 'slot_state.writing', raw: status, tone: 'info' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function gateSlotFocus(state: GateSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

/** What the Node is: a Graph gate, a Custom card, a Node with a document of an open type, or nothing to draw. */
export function gateCardShape(facts: GateCardFacts): GateCardShape {
  if (facts.nodeType === 'gate') return 'gate'
  if (facts.nodeType === 'custom') return 'custom'
  if (facts.docId !== null) return 'document'
  return 'none'
}

function filled(key: GateSlot['key'], id: string, reviewStatus: string | null, docStatus: string | null): GateSlot {
  const state = gateDocumentState(reviewStatus, docStatus)
  return { key, kind: 'document', id, empty: false, emptyKey: null, state, focus: gateSlotFocus(state), open: 'enabled' }
}

/** A Custom card's line: its card line's document, else none (a hollow dot; the report one waits for a run). */
function customLine(key: 'instruction' | 'report', facts: GateCardFacts): GateSlot {
  const own = facts.cardDocuments.find((line) => line.slot === key)
  if (own?.docId) return filled(key, own.docId, own.docReviewStatus, own.status)
  return { key, kind: 'document', id: null, empty: true, emptyKey: key === 'report' && !own ? 'empty.after_run' : null, state: null, focus: false, open: 'none' }
}

export function gateCardSlots(facts: GateCardFacts, instructionOmitted = false): readonly GateSlot[] {
  switch (gateCardShape(facts)) {
    case 'gate':
      return [{ key: 'result', kind: 'result', id: null, empty: true, emptyKey: 'empty.before', state: null, focus: false, open: 'none' }]
    case 'custom': {
      const report = customLine('report', facts)
      return instructionOmitted ? [report] : [customLine('instruction', facts), report]
    }
    case 'document':
      return [filled('document', facts.docId!, facts.docReviewStatus, facts.docStatus)]
    default:
      return []
  }
}

export type GateOperation = 'confirm' | 'reject'

export const GATE_OPERATIONS: readonly GateOperation[] = ['confirm', 'reject']

export interface GateActionItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.gateCard.`) of why it is disabled. */
  readonly reason: string | null
}

/** The two header actions of a gate: shown, never usable yet (no gate confirm / reject API). */
export function gateActions(facts: GateCardFacts): Readonly<Record<GateOperation, GateActionItem>> {
  const gate = gateCardShape(facts) === 'gate'
  const item: GateActionItem = { visible: gate, enabled: false, reason: gate ? 'reason.unconnected' : null }
  return { confirm: item, reject: item }
}
