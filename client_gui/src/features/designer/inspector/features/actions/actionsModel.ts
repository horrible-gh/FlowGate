// flowgate.v02.0048 T#6 — the header actions' availability rules (D#1 §8, §10.1 헤더 액션 column,
// §10.2 T#6 row). Pure functions: card kind × review status × ownership / read only × AI running
// × group disposed × an action in flight -> per operation visible / enabled / reason, and a
// refused call -> the reason shown before the state is read again.
//
// flowgate.v02.0056 F2-1: the document approve / reject operations are gone from the header. A v0.2 card is
// approved as a whole with its own [승인] (cardApproval, the Canvas card — O1 0005-TR §4-1) and has no user
// [반려]; the gate's ✓ 확인 · ✕ 반려 stay the gate card's. AC is the check card's own final approval (T#12).
//
// Operations (all change operations, D#1 §8.3 rule 0):
//   run               v0.1 single run (ai-invoke edit): the AI works on this card's document again.
//                     v01 only (rules 2-4), work cards N/NR · T… · DS… (R/B ▶ is AI delegation,
//                     §8.4), while the document is neither approved nor rejected.
//   rework            v0.1 rework (ai-invoke rework) of a rejected document, v01 only, the same
//                     cards. "Reject, then run again" is these two steps; there is no combined button.
// Not offered at all (D#1 §8.4): Graph Node run / re-run / retry, WP expansion, AI delegation — no disabled
// place-holder either. flowgate.v02.0053 T#9: the Graph run itself is the top band's (다음 단계 · 전체 실행 ·
// 중지, center/run/flowRunControl.ts), and the gate decision is the 확인 card's own ✓ 확인 · ✕ 반려 (a gate
// has no document, so it is not a card of this feature: gateCard/useGateDecision.ts).
//
// Permissions are the server's (403 is shown as a reason); no operation here is admin only.
import type { InspectorCardKind, InspectorReadOnly } from '../../inspectorTarget'

export type ActionsOperation = 'run' | 'rework'

export const ACTIONS_OPERATIONS: readonly ActionsOperation[] = ['run', 'rework']

/** Every operation changes the server state (D#1 §8.3 rule 0). */
export const ACTIONS_OPERATION_CLASS: Readonly<Record<ActionsOperation, 'change' | 'query'>> = {
  run: 'change',
  rework: 'change',
}

/** Cards a v0.1 single run / rework is offered on (D#1 §10.1 "실행·재작업 v01"). */
export const ACTIONS_RUN_CARDS: readonly InspectorCardKind[] = ['research', 'task', 'design']

/** Review statuses a run does not apply to: approved (done) and rejected (rework instead). */
const RUN_CLOSED: readonly string[] = ['approved', 'wf_done', 'rejected']

export interface ActionsInput {
  readonly cardKind: InspectorCardKind
  readonly ownership: string | null
  readonly readOnly: InspectorReadOnly
  /** The latest known review status (the read document, else the Canvas). */
  readonly docReviewStatus: string | null
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
  /** One of the operations is waiting for its answer. */
  readonly busy: boolean
}

export interface ActionsAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.actions.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type ActionsAvailability = Readonly<Record<ActionsOperation, ActionsAvailabilityItem>>

const HIDDEN: ActionsAvailabilityItem = { visible: false, enabled: false, reason: null }

function visibleOf(operation: ActionsOperation, input: ActionsInput): boolean {
  const status = input.docReviewStatus ?? ''
  switch (operation) {
    case 'run':
      return input.ownership === 'v01' && ACTIONS_RUN_CARDS.includes(input.cardKind) && !RUN_CLOSED.includes(status)
    case 'rework':
      return input.ownership === 'v01' && ACTIONS_RUN_CARDS.includes(input.cardKind) && status === 'rejected'
  }
}

/**
 * Why a change operation is disabled: one reason, in the order of D#1 §8.3 rule 6-1 — ownership
 * conflict, revision view, disposed, AI running, then this feature's own (an action in flight).
 */
export function actionsDisabledReason(input: ActionsInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed) return 'reason.disposed'
  if (input.aiRunning) return 'reason.ai_running'
  if (input.busy) return 'reason.busy'
  return null
}

/** Per operation: shown at all, usable now, and why not. */
export function actionsAvailability(input: ActionsInput): ActionsAvailability {
  const reason = actionsDisabledReason(input)
  const item = (operation: ActionsOperation): ActionsAvailabilityItem =>
    visibleOf(operation, input) ? { visible: true, enabled: reason === null, reason } : HIDDEN
  return { run: item('run'), rework: item('rework') }
}

/** The review status the rules read: the read document's when it is the target's, else the Canvas one. */
export function actionsReviewStatus(
  target: { readonly docId: string | null; readonly docReviewStatus: string | null },
  read: { readonly docId: string; readonly docReviewStatus: string | null } | null,
): string | null {
  return read && read.docId === target.docId ? read.docReviewStatus : target.docReviewStatus
}

/** Kinds of a refused call (the `@/features/document` reviewErrorOf kinds). */
export type ActionsFailureKind = 'running' | 'disposed' | 'conflict' | 'forbidden' | 'not_found' | 'invalid' | 'network' | 'other'

/** Message key of a refused call; the state is read again after it is shown. */
export function actionsFailureKey(kind: ActionsFailureKind): string {
  return `failure.${kind}`
}
