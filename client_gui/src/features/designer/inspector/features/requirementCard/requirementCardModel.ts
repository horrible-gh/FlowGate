// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's own lines (0035 v7 req.*): one document slot,
// 요구사항 or 버그 by the document type. Pure functions over the target; nothing reads the server.
// flowgate.v02.0053 T#5 (0053 CH) — 요구사항 · AI delegation (only while AI delegation is on): the lines are the card's
// documents the server keeps (card lines). The AI delegation engine is not in this range — its line is
// the empty record document the setting made, opened like any other.
import type { InspectorTarget } from '../../inspectorTarget'

export type RequirementSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface RequirementSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: RequirementSlotTone
}

export interface RequirementSlot {
  /** `document`: the root document line (요구사항 / 버그); `delegation`: the AI delegation line. */
  readonly key: 'document' | 'delegation'
  readonly kind: 'document' | 'result'
  /** Which name the line carries. */
  readonly name: 'requirement' | 'bug' | 'delegation'
  readonly id: string | null
  readonly empty: boolean
  /** Message key (under `gui.inspector.requirementCard.`) of why the line has no document, or null. */
  readonly emptyKey: string | null
  readonly state: RequirementSlotState | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
}

export type RequirementCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus' | 'cardDocuments'>

const REVIEW_TONES: Readonly<Record<string, RequirementSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** the workflow states a root R / B takes after approval. The slot pill shows
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
  // a document being written asks nothing of the operator — a neutral state draws no badge.
  return { key: 'slot_state.writing', raw: status, tone: 'neutral' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function requirementSlotFocus(state: RequirementSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

/** The line name: 버그 for a B document, 요구사항 otherwise. */
export function requirementSlotName(docType: string | null | undefined): RequirementSlot['name'] {
  return docType?.trim().toUpperCase() === 'B' ? 'bug' : 'requirement'
}

function filled(key: RequirementSlot['key'], name: RequirementSlot['name'], id: string, reviewStatus: string | null, docStatus: string | null): RequirementSlot {
  const state = requirementDocumentState(reviewStatus, docStatus)
  return { key, kind: key === 'document' ? 'document' : 'result', name, id, empty: false, emptyKey: null, state, focus: requirementSlotFocus(state), open: 'enabled' }
}

/**
 * The card's lines: the root document (its card line, else the bound document), and the AI delegation line
 * while the card has one — the server keeps it only while AI delegation is on, so it is never drawn empty.
 * A requirement card whose Flow root is held by another card has no document on its line.
 */
export function requirementCardSlots(facts: RequirementCardFacts): readonly RequirementSlot[] {
  const own = facts.cardDocuments.find((line) => line.slot === 'requirement')
  const name = requirementSlotName(own?.docType ?? facts.docType)
  const id = own ? own.docId : facts.docId
  const root: RequirementSlot = id
    ? own
      ? filled('document', name, id, own.docReviewStatus, own.status)
      : filled('document', name, id, facts.docReviewStatus, facts.docStatus)
    : { key: 'document', kind: 'document', name, id: null, empty: true, emptyKey: null, state: null, focus: false, open: 'none' }
  const extra = facts.cardDocuments.find((line) => line.slot === 'delegation' && line.docId !== null)
  return extra?.docId ? [root, filled('delegation', 'delegation', extra.docId, extra.docReviewStatus, extra.status)] : [root]
}
