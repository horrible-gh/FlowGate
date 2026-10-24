// flowgate.v02.0053 T#9 — the Graph run as the Inspector's 확인 card acts on it (P0008 §7 S5-4 / S6-1). No
// new server API: the Graph run routes (server graph_workflow/api/run_routes.py).
//
//   current   GET  /api/v1/flows/{flow_id}/runs/current -> { run: { run_id, status, node_runs[] } | null }
//   decide    POST /api/v1/runs/{run_id}/nodes/{node_key}/gate-decision { decision, comment?, request_id?, expected_attempt_id?, expected_settings_version? } -> 200/202 { run, decision?, job? }
//
// `decision` is `confirm` | `reject`; the comment (a reject's reason) is at most 500 characters. A refusal
// is thrown as it is (see documentErrorOf): 409 `run_not_running` (a stopped run takes no decision),
// `node_not_open`, `node_not_gate`, `run_policy_undecided` (the gate rule is not decided yet) …
//
// flowgate.v02.0056 F2-1 — a card's [승인] from the Inspector (O1 0005-TR §4-1):
//
//   approve   POST /api/v1/runs/{run_id}/nodes/{node_key}/approval { result_doc_id } -> { run, approval }
//
// The node rows also carry `awaiting_approval` / `stage` / `result_doc_id` and the run its pending propagation
// (O1 / O2 §4-2), each kept only when sent. A refused approval is read by runCardApprovalErrorOf: the code, the
// status, the server's message as sent (x-locale) and the params a refusal names (the refused document, why,
// the documents approved before it, the node's current result).
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

/** The longest comment the server takes (run_request.RUN_REASON_MAX_LEN). */
export const RUN_COMMENT_MAX_LENGTH = 500

export type RunGateDecision = 'confirm' | 'reject'

export interface RunNodeState {
  readonly nodeKey: string
  /** `pending` | `open` | `review` | `done` | `rejected` … as sent. */
  readonly status: string
  readonly attemptId?: string | null
  readonly gate?: RunGateState | null
  /** flowgate.v02.0056 F2-1: the node's result document; absent when not sent. */
  readonly resultDocId?: string | null
  /** flowgate.v02.0056 F2-1: the card waits for its approval; absent when not sent. */
  readonly awaitingApproval?: boolean
  /** flowgate.v02.0056 F2-1: `instruction` | `result` | null; absent when not sent. */
  readonly stage?: string | null
}

export interface RunCurrent {
  readonly runId: string
  /** `running` | `stopped` | `completed` as sent. */
  readonly status: string
  readonly idleReason?: string | null
  readonly nodes: readonly RunNodeState[]
  /** flowgate.v02.0056 F2-1: cards approved while the run was stopped (their successors wait); absent when not sent. */
  readonly pendingPropagationCount?: number
  readonly pendingNodeKeys?: readonly string[]
}

type Raw = Record<string, unknown>

/** The server's `run` object -> RunCurrent, or null. */
export function toRunCurrent(raw: unknown): RunCurrent | null {
  if (!raw || typeof raw !== 'object') return null
  const run = raw as Raw
  if (typeof run.run_id !== 'string' || !run.run_id) return null
  const rows = Array.isArray(run.node_runs) ? run.node_runs : []
  return {
    runId: run.run_id,
    status: typeof run.status === 'string' ? run.status : '',
    ...('idle_reason' in run ? { idleReason: text(run.idle_reason) } : {}),
    nodes: rows
      .filter((row): row is Raw => !!row && typeof row === 'object' && typeof (row as Raw).node_key === 'string')
      .map((row) => ({
        nodeKey: row.node_key as string,
        status: typeof row.status === 'string' ? row.status : '',
        ...('attempt_id' in row ? { attemptId: text(row.attempt_id) } : {}),
        ...('gate' in row ? { gate: toRunGateState(row.gate) } : {}),
        ...('result_doc_id' in row ? { resultDocId: text(row.result_doc_id) } : {}),
        ...(typeof row.awaiting_approval === 'boolean' ? { awaitingApproval: row.awaiting_approval } : {}),
        ...('stage' in row ? { stage: text(row.stage) } : {}),
      })),
    ...(typeof run.pending_propagation_count === 'number' ? { pendingPropagationCount: run.pending_propagation_count } : {}),
    ...(Array.isArray(run.pending_node_keys) ? { pendingNodeKeys: texts(run.pending_node_keys) } : {}),
  }
}

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const texts = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [])

/** The Flow's running or stopped run with its nodes, or null. */
export async function runCurrentRead(flowId: string): Promise<RunCurrent | null> {
  const response = await getRequest<Raw>(`${API}/flows/${encodeURIComponent(flowId)}/runs/current`)
  return toRunCurrent(response.data?.run)
}

/** 0083 WP-2: reset one idle open card; its next explicit resume allocates a new attempt. */
export async function runCardRecover(runId: string, nodeKey: string): Promise<RunCurrent | null> {
  const response = await postRequest<Raw>(`${API}/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeKey)}/recover`, {})
  return toRunCurrent(response.data?.run)
}

/** Decides an open gate of a running/idle run; preserves legacy comments and the optional versioned intent. */
export async function runGateDecide(runId: string, nodeKey: string, decision: RunGateDecision, comment?: string, intent?: RunGateIntent): Promise<RunCurrent | null> {
  const text = comment?.trim() ?? ''
  const response = await postRequest<Raw>(
    `${API}/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeKey)}/gate-decision`,
    { decision, ...(text ? { comment: text.slice(0, RUN_COMMENT_MAX_LENGTH) } : {}), ...intent },
  )
  return toRunCurrent(response.data?.run)
}

/** flowgate.v02.0056 F2-1: what a card approval did (O1 0005-TR §4-1 `approval`). */
export interface RunCardApproval {
  readonly nodeKey: string
  /** The card's documents in approval order (the instruction first, the result last). */
  readonly targetDocIds: readonly string[]
  readonly approvedDocIds: readonly string[]
}

/**
 * flowgate.v02.0056 F2-1 — approves a card waiting for its approval; `resultDocId` is the result the screen
 * showed (null: the node's current one). Answers the run after it and what it approved. A refusal is thrown as
 * it is (see runCardApprovalErrorOf).
 */
export async function runCardApprove(
  runId: string,
  nodeKey: string,
  resultDocId: string | null,
): Promise<{ readonly run: RunCurrent | null; readonly approval: RunCardApproval }> {
  const response = await postRequest<Raw>(`${API}/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeKey)}/approval`, {
    result_doc_id: resultDocId,
  })
  const approval = (response.data?.approval && typeof response.data.approval === 'object' ? response.data.approval : {}) as Raw
  return {
    run: toRunCurrent(response.data?.run),
    approval: {
      nodeKey: text(approval.node_key) ?? nodeKey,
      targetDocIds: texts(approval.target_doc_ids),
      approvedDocIds: texts(approval.approved_doc_ids),
    },
  }
}

/** flowgate.v02.0056 F2-1: a refused card approval as the screen reads it. */
export interface RunCardApprovalError {
  readonly code: string | null
  readonly status: number | null
  /** No answer reached the screen. */
  readonly network: boolean
  /** The server's message as it sent it (x-locale), or ''. */
  readonly message: string
  /** `document_approval_refused`: the refused document and why (`transition_refused`, a TR2 code …). */
  readonly docId: string | null
  readonly reason: string | null
  readonly detail: string | null
  /** `document_approval_refused`: the documents approved before the refusal. */
  readonly approvedDocIds: readonly string[]
  /** `result_changed`: the node's current result. */
  readonly resultDocId: string | null
}

/** What a failed runCardApprove threw: code, status, the server's message and the params it names. */
export function runCardApprovalErrorOf(error: unknown): RunCardApprovalError {
  const normalized = normalizeApiError(error)
  const source = normalized.source as Raw
  const params = (source.params && typeof source.params === 'object' ? source.params : {}) as Raw
  return {
    code: normalized.code,
    status: normalized.status,
    network: normalized.network,
    message: typeof source.message === 'string' ? source.message.trim() : '',
    docId: text(params.doc_id),
    reason: text(params.reason),
    detail: text(params.detail),
    approvedDocIds: texts(params.approved_doc_ids),
    resultDocId: text(params.result_doc_id),
  }
}

// D0007 §6/7.2: keep the server's gate facts; no client-side success inference.
export interface RunGateIntent {
  readonly request_id: string
  readonly expected_attempt_id: string
  readonly expected_settings_version: number
}
export interface RunGateRecovery {
  readonly decision_id: string
  readonly decision_version: number
  readonly status: string
  readonly retryable: boolean
  readonly cancelable: boolean
  readonly resolvable: boolean
  readonly evidence_version: number
  readonly evidence_refs: readonly string[]
  readonly phase: string | null
  readonly error_code: string | null
}
export interface RunGateResult {
  readonly decision_id: string
  readonly attempt_id: string | null
  readonly result: string
  readonly status: string
  readonly actor_user_id: string | null
  readonly decided_at: string | null
  readonly completed_at: string | null
  readonly comment: string | null
  readonly archived: boolean
  readonly archive_id: string | null
  readonly effects: readonly Record<string, unknown>[] | null
}
export interface RunGateState {
  readonly decision: RunGateResult | null
  readonly decision_in_progress: boolean
  readonly can_decide: boolean
  readonly settings_locked: boolean
  readonly archived: boolean
  readonly recovery_reset_required: boolean
  readonly recovery: RunGateRecovery | null
  readonly job: { readonly job_id: string; readonly status: string; readonly phase: string | null; readonly last_error_code: string | null } | null
}
export function toRunGateState(raw: unknown): RunGateState | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Raw
  // The server owns these versioned read contracts, including phase evidence and receipt ids.
  return {
    decision: (row.decision ?? null) as RunGateResult | null,
    decision_in_progress: row.decision_in_progress === true,
    can_decide: row.can_decide === true,
    settings_locked: row.settings_locked === true,
    archived: row.archived === true,
    recovery_reset_required: row.recovery_reset_required === true,
    recovery: (row.recovery ?? null) as RunGateRecovery | null,
    job: (row.job ?? null) as RunGateState['job'],
  }
}
export interface RunGateHistory {
  readonly items: readonly RunGateResult[]
  readonly latest: RunGateResult | null
  readonly next_cursor: string | null
}
const gatePath = (runId: string, nodeKey: string) => `${API}/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeKey)}`
export async function runGateHistoryRead(runId: string, nodeKey: string): Promise<RunGateHistory> {
  const response = await getRequest<RunGateHistory>(`${gatePath(runId, nodeKey)}/gate-decisions`)
  return response.data
}
export interface RunGateRecoveryRequest {
  readonly request_id: string
  readonly expected_decision_version: number
  readonly resolution?: 'effects_confirmed' | 'abandon'
  readonly reason?: string
  readonly evidence_version?: number
  readonly evidence_refs?: readonly string[]
}
export async function runGateRecover(runId: string, nodeKey: string, decisionId: string, action: 'retry' | 'cancel' | 'resolve', body: RunGateRecoveryRequest): Promise<RunCurrent | null> {
  const response = await postRequest<Raw>(`${gatePath(runId, nodeKey)}/gate-decisions/${encodeURIComponent(decisionId)}/${action}`, body)
  return toRunCurrent(response.data?.run)
}
