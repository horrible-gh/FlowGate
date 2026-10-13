// flowgate.v02.0056 F2-1 — the card approval bar's rules (O1 0005-TR §4-1 / §4-2). Pure functions over the card
// state (inspectorCardState — the Canvas card's rule), whether the Flow is read only and whether an approval is
// being answered. What a state is called and how a refusal is grouped are decided in center/graph/cardStateView.ts
// (one place for the Canvas and the Inspector); this file only decides what the bar shows and allows.
//
//   shown         a Graph card with a run (any state: the bar names it)
//   [승인]        only while the card waits for its approval — never on 작업 중, 승인됨 or a rejected R/B root
//   usable        not an ownership conflict, no approval of it being answered
//
// A card waiting for its approval may already hold an approved result (only its instruction is left): the bar
// follows the card state, each document line keeps its own review state.
import type { CardApprovalFailureView, CardStateView, InspectorReadOnly } from '../../inspectorTarget'

export interface CardApprovalInput {
  readonly state: CardStateView | null
  readonly readOnly: InspectorReadOnly
  readonly busy: boolean
}

export interface CardApprovalBarView {
  readonly shown: boolean
  /** [승인] is on the bar. */
  readonly approveVisible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.cardApproval.`) of why [승인] cannot be pressed, or null. */
  readonly reason: string | null
}

const HIDDEN: CardApprovalBarView = { shown: false, approveVisible: false, enabled: false, reason: null }

/** Why [승인] cannot be pressed now, or null. An ownership conflict first, then an approval being answered. */
export function cardApprovalDisabledReason(input: CardApprovalInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.busy) return 'reason.busy'
  return null
}

export function cardApprovalBar(input: CardApprovalInput): CardApprovalBarView {
  if (!input.state) return HIDDEN
  if (!input.state.approvable) return { shown: true, approveVisible: false, enabled: false, reason: null }
  const reason = cardApprovalDisabledReason(input)
  return { shown: true, approveVisible: true, enabled: reason === null, reason }
}

/** A refused [승인] as the bar keeps it. */
export interface CardApprovalFailure {
  readonly view: CardApprovalFailureView
  readonly code: string | null
  /** The server's message as it sent it (x-locale), or ''. */
  readonly message: string
  /** `document_approval_refused`: the refused document, why, and the documents approved before it. */
  readonly docId: string | null
  readonly reason: string | null
  readonly approvedDocIds: readonly string[]
}

/** The refusal is told in a toast and the button stays (busy: the same press may work a moment later). */
export function cardApprovalToasted(failure: CardApprovalFailure | null): boolean {
  return failure !== null && failure.view.kind === 'busy'
}

/** The tone of the bar's refusal line: a moved card is told as information, anything else as a failure. */
export function cardApprovalFailureTone(failure: CardApprovalFailure): 'muted' | 'warning' | 'danger' {
  if (failure.view.kind === 'changed' || failure.view.kind === 'stale') return 'warning'
  return 'danger'
}

/** The server's message when it says more than the code's own sentence (shown as sent). */
export function cardApprovalServerMessage(failure: CardApprovalFailure, sentence: string): string | null {
  const message = failure.message.trim()
  return message && message !== failure.code && message !== sentence ? message : null
}
