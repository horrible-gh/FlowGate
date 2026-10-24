// D0007 §6: gate result and document-tab actions; custom/document card lines retain their existing behavior.
import type { RunGateState } from '@/features/document'
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
  // flowgate.v02.0056 F1: a document being written asks nothing of the operator — a neutral state draws no badge.
  return { key: 'slot_state.writing', raw: status, tone: 'neutral' }
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

/** The Flow's run as the gate decision reads it (features/document `RunCurrent`). */
export interface GateRunFacts {
  readonly status: string
  readonly nodes: ReadonlyArray<{ readonly nodeKey: string; readonly status: string; readonly gate?: RunGateState | null }>
}

/** Why a gate decision cannot be taken now, or null (it can). */
export function gateDecisionReason(
  target: Pick<InspectorTarget, 'nodeId' | 'readOnly'>,
  run: GateRunFacts | null,
  busy: boolean,
): string | null {
  if (target.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (!run || !['running', 'idle'].includes(run.status)) return 'reason.not_running'
  if (run.nodes.find((node) => node.nodeKey === target.nodeId)?.status !== 'open') return 'reason.not_open'
  if (busy) return 'reason.busy'
  if (run.nodes.find(node => node.nodeKey === target.nodeId)?.gate?.can_decide !== true) return 'reason.not_open'
  return null
}

/** Document-tab actions: open + server can_decide, running or idle; hidden after a decision. */
export function gateActions(
  facts: GateCardFacts & Partial<Pick<InspectorTarget, 'nodeId' | 'readOnly'>>,
  run: GateRunFacts | null = null,
  busy = false,
): Readonly<Record<GateOperation, GateActionItem>> {
  if (gateCardShape(facts) !== 'gate') {
    const hidden: GateActionItem = { visible: false, enabled: false, reason: null }
    return { confirm: hidden, reject: hidden }
  }
  const reason = gateDecisionReason({ nodeId: facts.nodeId ?? '', readOnly: facts.readOnly ?? null }, run, busy)
  const status = run?.nodes.find(node => node.nodeKey === facts.nodeId)?.status
  const item: GateActionItem = { visible: status !== 'done' && status !== 'rejected', enabled: reason === null, reason }
  return { confirm: item, reject: item }
}

/** What a refused gate decision shows (the server's code, else the status). */
export function gateFailureKey(error: { readonly kind: string; readonly status: number | null; readonly code: string | null }): string {
  switch (error.code) {
    case 'gate_settings_invalid':
      return 'failure.gate_settings_invalid'
    case 'gate_git_unavailable':
      return 'failure.gate_git_unavailable'
    case 'gate_context_changed':
      return 'failure.gate_context_changed'
    case 'gate_decision_in_progress':
      return 'failure.gate_decision_in_progress'
    case 'gate_request_conflict':
      return 'failure.gate_request_conflict'

    case 'run_not_running':
      return 'failure.not_running'
    case 'node_not_open':
    case 'node_not_gate':
      return 'failure.not_open'
    case 'run_policy_undecided':
      return 'failure.undecided'
  }
  if (error.kind === 'network') return 'failure.network'
  if (error.status === 403) return 'failure.forbidden'
  return 'failure.other'
}

/** Only the current successful decision's own completed archive receipt can light the chip. */
export function gateResult(node: { status: string; attemptId?: string | null; gate?: RunGateState | null } | null) {
  const gate = node?.gate
  const result = gate?.decision
  const finished = !gate?.decision_in_progress && !gate?.recovery_reset_required
  const confirmed = finished && node?.status === 'done'
  const rejected = finished && node?.status === 'rejected'
  const current = !!result && (result.attempt_id === node?.attemptId || result.attempt_id === null)
  return {
    key: confirmed ? 'confirmed' : rejected ? 'rejected' : 'before',
    tone: confirmed ? 'success' : rejected ? 'danger' : 'neutral',
    archived: !!(confirmed && current && gate?.archived && result?.archived && result.archive_id && result.result === 'confirmed' && result.status === 'succeeded'),
    description: current && (confirmed || rejected) ? [result.actor_user_id, result.completed_at ?? result.decided_at, result.comment].filter(Boolean).join(' · ') : '',
  }
}
