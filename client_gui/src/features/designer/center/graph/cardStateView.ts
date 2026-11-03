// v0.2 card presentation: automatic completion, intervention and system errors.
/** The node run facts the state is read from (FlowGraphNodeRun, the Canvas `node_run`). */
export interface CardRunFacts {
  readonly intervention?: { readonly kind: string; readonly reason: string | null } | null
  readonly waitReason?: { code: string; detail?: string | null; role?: string | null } | null
  readonly dispatchErrorCode?: string | null
  /** flowgate.v02.0121 T#4: the failed AI call's short cause (`provider_capacity`, ...); absent: none told. */
  readonly dispatchErrorDetail?: string | null
  readonly resultDocId?: string | null
  readonly status: string | null
  readonly awaitingApproval?: boolean
  readonly stage?: string | null
  /** flowgate.v02.0083 T#4: the node attempt's running / requested AI executions (absent: not known). */
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  /** flowgate.v02.0083 T#4: the card's document holds a question still unanswered. */
  readonly question?: boolean
  /** flowgate.v02.0105 T#2 (B-2): why the result was not approved automatically, or null / absent. */
  readonly approvalWaitReason?: string | null
  /** flowgate.v02.0113 T#1: the card is actually running (the server's `node_running`); absent: not known. */
  readonly nodeRunning?: boolean
}

export const CARD_STATE_KEYS = ['instruction_write_wait', 'result_write_wait', 'waiting', 'manual_write_wait', 'ai_setup_required', 'ai_unavailable', 'no_result', 'incomplete', 'waiting_answer', 'waiting_writer', 'writing_instruction', 'writing_result', 'working', 'needs_intervention', 'system_error', 'approved', 'rejected', 'pending', 'closed'] as const

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
  /** The card may be approved now ([승인] is shown when the server allows the user): awaiting approval only. */
  readonly approvable: boolean
  /** flowgate.v02.0105 T#2 (B-5): why an awaiting card was not approved automatically (tooltip only), else null. */
  readonly reason: string | null
}

const view = (key: CardStateKey, phase: CardStatePhase, tone: CardStateTone, reason: string | null = null): CardStateView => ({
  key,
  phase,
  tone,
  labelKey: `gui.designer.center.card_states.${key}`,
  approvable: false,
  reason,
})

/**
 * flowgate.v02.0083 T#4: an AI execution of the node runs or is requested; null when the server sent no counts.
 * flowgate.v02.0113 T#1: the server's `node_running` (actual running, its AI review included) decides when it is sent.
 */
function executing(run: CardRunFacts): boolean | null {
  if (typeof run.nodeRunning === 'boolean') return run.nodeRunning
  if (run.runningExecutionCount === undefined && run.requestedExecutionCount === undefined) return null
  return (run.runningExecutionCount ?? 0) + (run.requestedExecutionCount ?? 0) > 0
}

/** flowgate.v02.0113 T#1: a person's writing wait is named by the role that writes (지시서 / 결과 작성 대기). */
const WRITE_WAIT_LABELS: Readonly<Record<string, CardStateKey>> = { instruction: 'instruction_write_wait', execution: 'result_write_wait' }

/** flowgate.v02.0121 T#4: the short causes of a failed AI call (an `incomplete` card's reason, the node wire's
 * `dispatch.error_detail`) that are worded as a sentence of their own (`gui.designer.center.card_failure_reasons.*`). */
export const CARD_FAILURE_REASONS = ['provider_capacity', 'provider_failed', 'timeout', 'providers_exhausted'] as const

/** flowgate.v02.0125 T#2: a `needs_intervention` card's reason worded as a sentence of its own, by message key
 * (`gui.designer.center.card_intervention_reasons.*`). Q #544: an exhausted review is re-reviewed by `다음 단계` / `전체 실행`. */
export const CARD_INTERVENTION_REASONS: Readonly<Record<string, string>> = {
  'review_not_passed:review_count_exhausted': 'review_count_exhausted',
}

/**
 * flowgate.v02.0121 T#4 (rej_01M6FFMXBCPX855V): the shown text of a card state's reason, one rule for every screen that
 * shows a `CardStateView` (the Canvas card title, the Inspector state pill): an `incomplete` card's known cause as its
 * ko / en / ja sentence; any other reason (an intervention's, an approval wait's, a cause this screen does not know) as
 * sent; null when the state has no reason.
 */
export function cardStateReasonText(
  state: { readonly key: string; readonly reason?: string | null } | null | undefined,
  t: (key: string) => string,
): string | null {
  if (!state || !state.reason) return null
  if (state.key === 'incomplete' && (CARD_FAILURE_REASONS as readonly string[]).includes(state.reason)) {
    return t(`gui.designer.center.card_failure_reasons.${state.reason}`)
  }
  if (state.key === 'needs_intervention' && Object.prototype.hasOwnProperty.call(CARD_INTERVENTION_REASONS, state.reason)) {
    return t(`gui.designer.center.card_intervention_reasons.${CARD_INTERVENTION_REASONS[state.reason]}`)
  }
  return state.reason
}

/** The card state of a node run, or null (no run, or a status this screen does not know). */
export function cardStateView(run: CardRunFacts | null | undefined): CardStateView | null {
  const status = run?.status?.trim() ?? ''
  const wait = run?.waitReason
  if ((status === 'open' || status === 'review') && run?.intervention) {
    const error = run.intervention.kind === 'system_error'
    return view(error ? 'system_error' : 'needs_intervention', 'other', error ? 'danger' : 'warning', run.intervention.reason)
  }
  // 0113 labels override presentation only. A 0105 approval remains available with its raw reason.
  // An absent/null/unknown new wait reason takes the unchanged 0083/0105 branches below.
  if ((status === 'open' || status === 'review') && wait &&
      ['manual_write_wait', 'ai_setup_required', 'ai_unavailable'].includes(wait.code)) {
    const key = run?.question === true ? 'waiting_answer' : wait.code as CardStateKey
    const tone = key === 'ai_unavailable' ? 'danger' : key === 'ai_setup_required' ? 'warning' : 'neutral'
    const reason = wait.detail || wait.role || null
    const named = key === 'manual_write_wait' ? WRITE_WAIT_LABELS[wait.role ?? ''] : undefined
    const shown = view(key, 'awaiting', tone, reason)
    return { ...shown, ...(named ? { labelKey: `gui.designer.center.card_states.${named}` } : {}), approvable: false }
  }
  switch (status) {
    case 'done':
      return view('approved', 'approved', 'success')
    case 'review': {
      if (run?.awaitingApproval !== true) return run?.nodeRunning === false ? view('waiting', 'awaiting', 'neutral') : view('working', 'working', 'info')
      const reason = run.approvalWaitReason?.trim() || null
      return view('needs_intervention', 'other', 'warning', reason || 'completion_missing')
    }
    case 'open':
      if (!run?.resultDocId && run?.dispatchErrorCode === 'no_result') return view('no_result', 'other', 'warning')
      // flowgate.v02.0121 T#4: its short cause as the reason (`card_failure_reasons`); the details stay in the logs.
      if (!run?.resultDocId && run?.dispatchErrorCode) return view('incomplete', 'other', 'warning', run.dispatchErrorDetail?.trim() || null)
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

/** Both card surfaces use the latest run read, falling back to the Canvas for omitted facts. */
export function cardNodeRun(canvas: import('./flowGraph').FlowGraphNodeRun | null | undefined, run: import('./flowGraphSource').FlowRunNodeInfo | undefined): import('./flowGraph').FlowGraphNodeRun | null {
  if (!run) return canvas ?? null
  return { openedAt: null, finishedAt: null, resultDocId: null, ...canvas, ...run }
}
