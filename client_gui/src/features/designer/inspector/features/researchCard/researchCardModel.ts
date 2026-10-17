// flowgate.v02.0051 stage 2 — the 조사 card's own lines (0035 v7 research.*: 지시서 + 레포트). Pure functions over
// the target: which lines the card shows, what each shows and whether it opens. Nothing here reads the
// server. The run / stop / history operations of the card have no Inspector API yet (D#1 §8.4).
// flowgate.v02.0053 T#5 (0053 CH) — the lines are the documents the server made when the card was placed
// (T#1 card lines: 지시서 N, 레포트 NR), so a placed card opens both at once. A Node without card
// lines (a document Node, a v0.1 sequence Node) shows its own document on its line and a Graph run's result
// on the report line; the other line has no document (a hollow dot saying why — no "not yet" text). The
// 지시서 line is hidden when the card's settings omit the instruction (v7).
import type { InspectorTarget } from '../../inspectorTarget'

export type ResearchSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

/** A slot line's state (the dot): a message key under `gui.designer.inspector.` with the raw server value. */
export interface ResearchSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: ResearchSlotTone
}

export interface ResearchSlot {
  readonly key: 'instruction' | 'report'
  readonly kind: 'document'
  readonly id: string | null
  readonly empty: boolean
  /** Message key (under `gui.inspector.researchCard.`) of why the line has no document, or null. */
  readonly emptyKey: string | null
  readonly state: ResearchSlotState | null
  /** The line the user should look at (a human decision is waiting). */
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
}

/** The parts of the target the card reads. */
export type ResearchCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus' | 'nodeRun' | 'cardDocuments'>

export const RESEARCH_INSTRUCTION_TYPES: readonly string[] = ['N']
export const RESEARCH_REPORT_TYPES: readonly string[] = ['NR']

const REVIEW_TONES: Readonly<Record<string, ResearchSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The state of a document: its review status, else its document status (draft / open / closed). */
export function researchDocumentState(reviewStatus: string | null, docStatus: string | null): ResearchSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  // flowgate.v02.0056 F1: a document being written asks nothing of the operator — a neutral state draws no badge.
  return { key: 'slot_state.writing', raw: status, tone: 'neutral' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function researchSlotFocus(state: ResearchSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

function filled(key: ResearchSlot['key'], id: string, reviewStatus: string | null, docStatus: string | null): ResearchSlot {
  const state = researchDocumentState(reviewStatus, docStatus)
  return { key, kind: 'document', id, empty: false, emptyKey: null, state, focus: researchSlotFocus(state), open: 'enabled' }
}

function emptyLine(key: ResearchSlot['key'], emptyKey: string | null): ResearchSlot {
  return { key, kind: 'document', id: null, empty: true, emptyKey, state: null, focus: false, open: 'none' }
}

/**
 * One line: its card line's document. Without card lines: the bound document of its type, the Graph
 * run's result on the report line (its state is not in the Canvas read), else no document — "실행 후" on
 * the report line, "연결 전" on the instruction line of a report Node.
 */
function researchLine(key: ResearchSlot['key'], types: readonly string[], facts: ResearchCardFacts): ResearchSlot {
  const own = facts.cardDocuments.find((line) => line.slot === key)
  if (own) return own.docId ? filled(key, own.docId, own.docReviewStatus, own.status) : emptyLine(key, null)
  const type = facts.docType?.trim().toUpperCase() ?? ''
  if (facts.docId !== null && types.includes(type)) return filled(key, facts.docId, facts.docReviewStatus, facts.docStatus)
  const result = key === 'report' ? (facts.nodeRun?.resultDocId ?? null) : null
  if (result !== null && result !== facts.docId) return { ...filled(key, result, null, null), state: null }
  return emptyLine(key, key === 'report' ? 'empty.after_run' : facts.docId !== null ? 'empty.unconnected' : null)
}

/** The card's lines: 지시서 (unless the settings omit it) · 레포트. */
export function researchCardSlots(facts: ResearchCardFacts, instructionOmitted = false): readonly ResearchSlot[] {
  const report = researchLine('report', RESEARCH_REPORT_TYPES, facts)
  return instructionOmitted ? [report] : [researchLine('instruction', RESEARCH_INSTRUCTION_TYPES, facts), report]
}
