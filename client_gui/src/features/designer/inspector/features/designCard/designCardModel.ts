// flowgate.v02.0051 stage 2 — the 설계 card's own lines. Pure functions over the target; nothing reads
// the server.
// flowgate.v02.0053 T#5 (0053 CH) — the 설계 card (기본 · 프로토콜 · 로직 · DB) is shaped like 조사 · 작업:
// 지시서 · 레포트, and the v7 "설명 대기" note is gone. The lines are the documents the server made when the
// card was placed (T#1 card lines: 지시서 DS, 레포트 D / P / L / DB). A Node without card lines (a design
// document Node) shows its own document on its line and a run's result on the report line. The 지시서
// line is hidden when the card's settings omit the instruction (v7).
import type { InspectorTarget } from '../../inspectorTarget'

export type DesignSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface DesignSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: DesignSlotTone
}

export interface DesignSlot {
  readonly key: 'instruction' | 'report'
  readonly kind: 'document'
  readonly id: string | null
  readonly empty: boolean
  /** Message key (under `gui.inspector.designCard.`) of why the line has no document, or null. */
  readonly emptyKey: string | null
  readonly state: DesignSlotState | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
}

export type DesignCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus' | 'nodeRun' | 'cardDocuments'>

export const DESIGN_INSTRUCTION_TYPES: readonly string[] = ['DS']
export const DESIGN_REPORT_TYPES: readonly string[] = ['D', 'P', 'L', 'DB']

const REVIEW_TONES: Readonly<Record<string, DesignSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The state of a document: its review status, else its document status (draft / open / closed). */
export function designDocumentState(reviewStatus: string | null, docStatus: string | null): DesignSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  // flowgate.v02.0056 F1: a document being written asks nothing of the operator — a neutral state draws no badge.
  return { key: 'slot_state.writing', raw: status, tone: 'neutral' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function designSlotFocus(state: DesignSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

function filled(key: DesignSlot['key'], id: string, reviewStatus: string | null, docStatus: string | null): DesignSlot {
  const state = designDocumentState(reviewStatus, docStatus)
  return { key, kind: 'document', id, empty: false, emptyKey: null, state, focus: designSlotFocus(state), open: 'enabled' }
}

function emptyLine(key: DesignSlot['key'], emptyKey: string | null): DesignSlot {
  return { key, kind: 'document', id: null, empty: true, emptyKey, state: null, focus: false, open: 'none' }
}

/** One line: its card line's document; without card lines, the bound document of its type or the run result. */
function designLine(key: DesignSlot['key'], types: readonly string[], facts: DesignCardFacts): DesignSlot {
  const own = facts.cardDocuments.find((line) => line.slot === key)
  if (own) return own.docId ? filled(key, own.docId, own.docReviewStatus, own.status) : emptyLine(key, null)
  const type = facts.docType?.trim().toUpperCase() ?? ''
  if (facts.docId !== null && types.includes(type)) return filled(key, facts.docId, facts.docReviewStatus, facts.docStatus)
  const result = key === 'report' ? (facts.nodeRun?.resultDocId ?? null) : null
  if (result !== null && result !== facts.docId) return { ...filled(key, result, null, null), state: null }
  return emptyLine(key, key === 'report' ? 'empty.after_run' : facts.docId !== null ? 'empty.unconnected' : null)
}

/** The card's lines: 설계 지시 (unless the settings omit it) · 설계서 (flowgate.v02.0074 T#3 names). */
export function designCardSlots(facts: DesignCardFacts, instructionOmitted = false): readonly DesignSlot[] {
  const report = designLine('report', DESIGN_REPORT_TYPES, facts)
  const instruction = designLine('instruction', DESIGN_INSTRUCTION_TYPES, facts)
  // Execution omission must not make an existing design instruction inaccessible.
  return instructionOmitted && instruction.id === null ? [report] : [instruction, report]
}
