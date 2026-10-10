// flowgate.v02.0051 stage 2 — the 설계 card's own lines. The 0035 v7 mockup left the 설계 card empty
// ("설명 대기": the result may be several sheets — D · L · P · DB — so whether one WP Step is one Node is
// not decided), and 0051 turn 7 says not to decide it here. So the card shows only what is not a
// decision: the v7 "not decided" note and the one document bound to the Node as a slot line. Pure
// functions over the target; nothing reads the server.
import type { InspectorTarget } from '../../inspectorTarget'

export type DesignSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface DesignSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: DesignSlotTone
}

export interface DesignSlot {
  readonly key: 'document'
  readonly kind: 'document'
  /** Document type of the line (DS / D / P / L / DB), for its name. */
  readonly type: string | null
  readonly id: string | null
  readonly empty: boolean
  readonly emptyKey: string | null
  readonly state: DesignSlotState | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
  readonly write: 'disabled' | 'none'
}

export type DesignCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus'>

export const DESIGN_DOC_TYPES: readonly string[] = ['DS', 'D', 'P', 'L', 'DB']

const REVIEW_TONES: Readonly<Record<string, DesignSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The pill of a bound document: its review status, else its document status (draft / open / closed). */
export function designDocumentState(reviewStatus: string | null, docStatus: string | null): DesignSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  return { key: 'slot_state.writing', raw: status, tone: 'info' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function designSlotFocus(state: DesignSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

/** The one line: the bound design document, or the empty line of a Node without one. */
export function designCardSlots(facts: DesignCardFacts): readonly DesignSlot[] {
  const type = facts.docType?.trim().toUpperCase() ?? ''
  const known = DESIGN_DOC_TYPES.includes(type) ? type : null
  if (facts.docId === null) {
    return [{ key: 'document', kind: 'document', type: known, id: null, empty: true, emptyKey: 'empty.not_yet', state: null, focus: false, open: 'none', write: 'disabled' }]
  }
  const state = designDocumentState(facts.docReviewStatus, facts.docStatus)
  return [{ key: 'document', kind: 'document', type: known, id: facts.docId, empty: false, emptyKey: null, state, focus: designSlotFocus(state), open: 'enabled', write: 'none' }]
}
