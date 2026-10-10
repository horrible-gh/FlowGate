// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's own lines (0035 v7 req.*): one document slot,
// 요구사항 or 버그 리포트 by the document type. The AI 위임 result line exists in v7 only while AI
// delegation is ON, and delegation has no API yet (0051-R §3.3), so the card draws the OFF shape: the
// one document line. Pure functions over the target; nothing reads the server.
import type { InspectorTarget } from '../../inspectorTarget'

export type RequirementSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface RequirementSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: RequirementSlotTone
}

export interface RequirementSlot {
  readonly key: 'document'
  readonly kind: 'document'
  /** Which name the line carries: the requirement or the bug report. */
  readonly name: 'requirement' | 'bug'
  readonly id: string | null
  readonly empty: boolean
  readonly emptyKey: string | null
  readonly state: RequirementSlotState | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
  readonly write: 'disabled' | 'none'
}

export type RequirementCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus'>

const REVIEW_TONES: Readonly<Record<string, RequirementSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** flowgate.v02.0052: the workflow states a root R / B takes after approval. The slot pill shows
 *  their plain wording, never the internal value (wf_in_progress / wf_done). */
const WORKFLOW_STATES: Readonly<Record<string, Pick<RequirementSlotState, 'key' | 'tone'>>> = {
  wf_in_progress: { key: 'slot_state.in_progress', tone: 'running' },
  wf_done: { key: 'slot_state.closed', tone: 'success' },
}

/** The pill of a bound document: its review status, else its document status (draft / open / closed). */
export function requirementDocumentState(reviewStatus: string | null, docStatus: string | null): RequirementSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  const workflow = WORKFLOW_STATES[review]
  if (workflow) return { ...workflow, raw: review }
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  return { key: 'slot_state.writing', raw: status, tone: 'info' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function requirementSlotFocus(state: RequirementSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

/** The line name: 버그 리포트 for a B document, 요구사항 otherwise. */
export function requirementSlotName(docType: string | null | undefined): RequirementSlot['name'] {
  return docType?.trim().toUpperCase() === 'B' ? 'bug' : 'requirement'
}

/** The one line: the bound root document, or the empty line of a Node without one. */
export function requirementCardSlots(facts: RequirementCardFacts): readonly RequirementSlot[] {
  const name = requirementSlotName(facts.docType)
  if (facts.docId === null) {
    return [{ key: 'document', kind: 'document', name, id: null, empty: true, emptyKey: 'empty.not_yet', state: null, focus: false, open: 'none', write: 'disabled' }]
  }
  const state = requirementDocumentState(facts.docReviewStatus, facts.docStatus)
  return [{ key: 'document', kind: 'document', name, id: facts.docId, empty: false, emptyKey: null, state, focus: requirementSlotFocus(state), open: 'enabled', write: 'none' }]
}
