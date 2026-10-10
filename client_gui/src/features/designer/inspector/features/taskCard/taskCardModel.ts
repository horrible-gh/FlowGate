// flowgate.v02.0051 stage 2 — the 작업 card's own lines (0035 v7 work.*: 지시서 (T) + 레포트 (TR); T2 /
// TR2 are the same pair). Pure functions over the target: which of the two slot lines the selected Node
// fills, what each shows and which button is usable. Nothing here reads the server — the sibling
// document of a pair is known only through the Graph node run's result document; where it is not known
// the line is drawn empty with the "not connected" wording (0051-R §2.3), never invented. The run /
// stop / history operations of the card have no Inspector API yet (D#1 §8.4). The v7 origin line
// (⇉ WP / 🤖 AI, "WP rev 1 · Step S1에서 전개") needs the WP deployment the Canvas read does not carry.
import type { InspectorTarget } from '../../inspectorTarget'

export type TaskSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

/** A slot line's state pill: a message key under `gui.designer.inspector.` with the raw server value. */
export interface TaskSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: TaskSlotTone
}

export interface TaskSlot {
  readonly key: 'instruction' | 'report'
  readonly kind: 'document' | 'result'
  readonly id: string | null
  readonly empty: boolean
  /** Message key (under `gui.inspector.taskCard.`) of the empty wording, or null. */
  readonly emptyKey: string | null
  readonly state: TaskSlotState | null
  /** Message key (under `gui.inspector.taskCard.`) of a summary after the pill, or null. */
  readonly summaryKey: string | null
  /** The line the user should look at (a human decision is waiting). */
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
  /** ＋: writing happens in the v0.1 screen, not here yet. */
  readonly write: 'disabled' | 'none'
}

/** The parts of the target the card reads. */
export type TaskCardFacts = Pick<InspectorTarget, 'docId' | 'docType' | 'docReviewStatus' | 'docStatus' | 'nodeRun'>

export const TASK_INSTRUCTION_TYPES: readonly string[] = ['T', 'T2']
export const TASK_REPORT_TYPES: readonly string[] = ['TR', 'TR2']

const REVIEW_TONES: Readonly<Record<string, TaskSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The pill of a bound document: its review status, else its document status (draft / open / closed). */
export function taskDocumentState(reviewStatus: string | null, docStatus: string | null): TaskSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  return { key: 'slot_state.writing', raw: status, tone: 'info' }
}

/** A human decision waits on the document (v7: the line is highlighted). */
export function taskSlotFocus(state: TaskSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}

function bound(key: TaskSlot['key'], kind: TaskSlot['kind'], facts: TaskCardFacts): TaskSlot {
  const state = taskDocumentState(facts.docReviewStatus, facts.docStatus)
  return { key, kind, id: facts.docId, empty: false, emptyKey: null, state, summaryKey: null, focus: taskSlotFocus(state), open: 'enabled', write: 'none' }
}

function known(key: TaskSlot['key'], kind: TaskSlot['kind'], id: string): TaskSlot {
  return { key, kind, id, empty: false, emptyKey: null, state: null, summaryKey: 'summary.not_read', focus: false, open: 'enabled', write: 'none' }
}

function emptyLine(key: TaskSlot['key'], kind: TaskSlot['kind'], emptyKey: string, write: TaskSlot['write'] = 'none'): TaskSlot {
  return { key, kind, id: null, empty: true, emptyKey, state: null, summaryKey: null, focus: false, open: 'none', write }
}

/**
 * The card's two lines. The selected Node's own document fills its line; the report of a T is the
 * Graph run's result document when there is one, else "실행 후" (v7); the instruction of a TR is not
 * linked in the Canvas read, so its line says so.
 */
export function taskCardSlots(facts: TaskCardFacts): readonly TaskSlot[] {
  const type = facts.docType?.trim().toUpperCase() ?? ''
  const isInstruction = TASK_INSTRUCTION_TYPES.includes(type)
  const isReport = TASK_REPORT_TYPES.includes(type)
  const hasDocument = facts.docId !== null
  const result = facts.nodeRun?.resultDocId ?? null

  const instruction: TaskSlot =
    isInstruction && hasDocument
      ? bound('instruction', 'document', facts)
      : isReport && hasDocument
        ? emptyLine('instruction', 'document', 'empty.unconnected')
        : emptyLine('instruction', 'document', 'empty.not_yet', 'disabled')

  const report: TaskSlot =
    isReport && hasDocument
      ? bound('report', 'document', facts)
      : result !== null && result !== facts.docId
        ? known('report', 'document', result)
        : emptyLine('report', 'document', 'empty.after_run')

  return [instruction, report]
}
