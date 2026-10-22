// flowgate.v02.0056 F2-1 — what a Graph card shows for its run, decided in one place for the Canvas card and
// the Inspector (0056 CH turn 5~10, O1 0005-TR §4-2, O2 0007-TR §4-2). Three states, by the node run alone:
//
//   작업 중   open (stage instruction: 지시서 작성 중 / stage result: 결과 작성 중), or review while an AI still
//             works on it (the node's run, its review loop: awaiting_approval false)
//   승인 대기 awaiting_approval true (a review node with a result no AI works on any more)
//   승인됨   done
//
// flowgate.v02.0083 T#4: an open card no AI execution works on (the node's running / requested executions are
// both 0) is not 작업 중 — it waits: for an answer to its document's question (응답 대기) or for its writer, a
// person or the next AI start (작성 대기). Its writer that ended without a result keeps 결과 없음 / 실행 미완료.
// Without the counts (an older server) the open card keeps 작업 중 as before.
//
// A rejected node (a rejected R/B root the run started with, a gate rejection) shows 반려됨 and never offers
// [승인]; pending / closed nodes show what they are. The [다음 단계] stop of the run is not a card state: a
// card approved (done) while the run is stopped stays 승인됨 — its successors wait in the run band, never as
// 승인 대기 here (`done` is read first). Each document line keeps its own review state (a card waiting for
// approval may already hold an approved result).
//
// Pure: no Vue, no i18n — the label is a message key under `gui.designer.center.card_states.`; the tone is the
// Canvas chip tone. The [승인] refusals (O1 4-1 codes) are grouped here too, so the Canvas, the run band toast
// and the Inspector approval bar handle a code the same way.

/** The node run facts the state is read from (FlowGraphNodeRun, the Canvas `node_run`). */
export interface CardRunFacts {
  readonly dispatchErrorCode?: string | null
  readonly resultDocId?: string | null
  readonly status: string | null
  readonly awaitingApproval?: boolean
  readonly stage?: string | null
  /** flowgate.v02.0083 T#4: the node attempt's running / requested AI executions (absent: not known). */
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  /** flowgate.v02.0083 T#4: the card's document holds a question still unanswered. */
  readonly question?: boolean
}

export const CARD_STATE_KEYS = ['no_result', 'incomplete', 'waiting_answer', 'waiting_writer', 'writing_instruction', 'writing_result', 'working', 'awaiting_approval', 'approved', 'rejected', 'pending', 'closed'] as const

export type CardStateKey = (typeof CARD_STATE_KEYS)[number]

export type CardStateTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

/** The three states the user is told apart (작업 중 · 승인 대기 · 승인됨), and the rest (0083 T#4: 응답 · 작성 대기 is awaiting). */
export type CardStatePhase = 'working' | 'awaiting' | 'approved' | 'other'

export interface CardStateView {
  readonly key: CardStateKey
  readonly phase: CardStatePhase
  readonly tone: CardStateTone
  /** Message key of the label. */
  readonly labelKey: string
  /** The card may be approved now ([승인] is shown): awaiting approval only. */
  readonly approvable: boolean
}

const view = (key: CardStateKey, phase: CardStatePhase, tone: CardStateTone): CardStateView => ({
  key,
  phase,
  tone,
  labelKey: `gui.designer.center.card_states.${key}`,
  approvable: key === 'awaiting_approval',
})

/** flowgate.v02.0083 T#4: an AI execution of the node runs or is requested; null when the server sent no counts. */
function executing(run: CardRunFacts): boolean | null {
  if (run.runningExecutionCount === undefined && run.requestedExecutionCount === undefined) return null
  return (run.runningExecutionCount ?? 0) + (run.requestedExecutionCount ?? 0) > 0
}

/** The card state of a node run, or null (no run, or a status this screen does not know). */
export function cardStateView(run: CardRunFacts | null | undefined): CardStateView | null {
  const status = run?.status?.trim() ?? ''
  switch (status) {
    case 'done':
      return view('approved', 'approved', 'success')
    case 'review':
      return run?.awaitingApproval === true ? view('awaiting_approval', 'awaiting', 'warning') : view('working', 'working', 'info')
    case 'open':
      if (!run?.resultDocId && run?.dispatchErrorCode === 'no_result') return view('no_result', 'other', 'warning')
      if (!run?.resultDocId && run?.dispatchErrorCode) return view('incomplete', 'other', 'warning')
      if (run && executing(run) === false) return view(run.question === true ? 'waiting_answer' : 'waiting_writer', 'awaiting', 'neutral')
      if (run?.stage === 'instruction') return view('writing_instruction', 'working', 'info')
      if (run?.stage === 'result') return view('writing_result', 'working', 'info')
      return view('working', 'working', 'info')
    case 'rejected':
      return view('rejected', 'other', 'danger')
    case 'pending':
      return view('pending', 'other', 'neutral')
    case 'closed':
      return view('closed', 'other', 'neutral')
    default:
      return null
  }
}

/**
 * What a refused [승인] (POST /runs/{run_id}/nodes/{node_key}/approval) asks of the screen:
 *   changed   409 result_changed — the result shown is not the node's any more: read the card again, tell it
 *   busy      409 group_ai_running · card_approval_busy · ai_running — keep the button, tell it (try later)
 *   stale     409 already_approved · node_not_in_review · no_result · node_not_started — read the card again
 *   refused   409 document_approval_refused — a document was refused (its id and reason are shown)
 *   forbidden 403 approval_forbidden (or no Flow access) — the approver lacks the permission
 *   inactive  409 run_not_active · flow_disposed · node_is_gate · Graph ownership — nothing to approve here
 *   other     anything else (no answer, an unknown code)
 */
export type CardApprovalFailureKind = 'changed' | 'busy' | 'stale' | 'refused' | 'forbidden' | 'inactive' | 'network' | 'other'

const FAILURE_KINDS: Readonly<Record<string, CardApprovalFailureKind>> = {
  result_changed: 'changed',
  group_ai_running: 'busy',
  card_approval_busy: 'busy',
  ai_running: 'busy',
  already_approved: 'stale',
  node_not_in_review: 'stale',
  no_result: 'stale',
  node_not_started: 'stale',
  node_not_found: 'stale',
  document_approval_refused: 'refused',
  approval_forbidden: 'forbidden',
  flow_access_denied: 'forbidden',
  token_kind_not_allowed: 'forbidden',
  run_not_active: 'inactive',
  run_not_found: 'inactive',
  flow_not_found: 'inactive',
  flow_disposed: 'inactive',
  node_is_gate: 'inactive',
  flow_owned_by_v01: 'inactive',
  flow_ownership_conflict: 'inactive',
}

export interface CardApprovalFailureView {
  readonly kind: CardApprovalFailureKind
  /** Message key of the code's own sentence (`gui.designer.center.approval.failure.<code>`), else the kind's. */
  readonly labelKey: string
  /** The Canvas and the run are read again after it (the card moved on the server). */
  readonly reread: boolean
  /** The [승인] button stays usable (busy: the same press may work a moment later). */
  readonly keepButton: boolean
}

/** Codes with a sentence of their own (the rest use their kind's). */
export const CARD_APPROVAL_CODES: readonly string[] = [
  'result_changed',
  'group_ai_running',
  'card_approval_busy',
  'ai_running',
  'already_approved',
  'node_not_in_review',
  'no_result',
  'node_not_started',
  'document_approval_refused',
  'approval_forbidden',
]

/** How a refused [승인] is handled and worded, by the server's code (403 without a known code: forbidden). */
export function cardApprovalFailureView(code: string | null, status: number | null = null, network = false): CardApprovalFailureView {
  let kind: CardApprovalFailureKind = network ? 'network' : code ? (FAILURE_KINDS[code] ?? 'other') : 'other'
  if (kind === 'other' && (status === 401 || status === 403)) kind = 'forbidden'
  const own = code !== null && CARD_APPROVAL_CODES.includes(code)
  return {
    kind,
    labelKey: `gui.designer.center.approval.failure.${own ? code : kind}`,
    reread: kind === 'changed' || kind === 'stale' || kind === 'refused' || kind === 'inactive',
    keepButton: kind === 'busy' || kind === 'network' || kind === 'other',
  }
}

/**
 * What a refusal names in its params (O1 4-1): for `document_approval_refused` the refused document, why
 * (`transition_refused`, a TR2 code …) and the documents approved before it; for `result_changed` the node's
 * current result. Kept with the refusal so every place that shows it (the Canvas toast, the Inspector bar) can.
 */
export interface CardApprovalRefusal {
  readonly docId: string | null
  readonly reason: string | null
  readonly detail: string | null
  readonly approvedDocIds: readonly string[]
  readonly resultDocId: string | null
}

const paramText = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null)

/** The refusal facts of a refused [승인]'s params (anything else: nothing named). */
export function cardApprovalRefusalOf(params: unknown): CardApprovalRefusal {
  const raw = (params && typeof params === 'object' ? params : {}) as Readonly<Record<string, unknown>>
  return {
    docId: paramText(raw.doc_id),
    reason: paramText(raw.reason),
    detail: paramText(raw.detail),
    approvedDocIds: Array.isArray(raw.approved_doc_ids) ? raw.approved_doc_ids.filter((id): id is string => typeof id === 'string' && !!id) : [],
    resultDocId: paramText(raw.result_doc_id),
  }
}
