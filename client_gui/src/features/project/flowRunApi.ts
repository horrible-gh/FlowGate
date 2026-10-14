// flowgate.v02.0053 T#9 — Graph run transport (P0008 §7 S5-1 ~ S5-5): start a Flow's run, read its
// current run, stop and resume a run. snake_case stays inside this file; every request carries this tab's
// `x-canvas-client-id` (flowCanvasApi.ts) like the Graph saver's. A refused request rejects with a
// FlowGraphApiError (flowGraphApi.ts): `code` / `params` are the server's (`run_policy_undecided`,
// `run_already_active`, `run_not_running`, `flow_owned_by_v01`, …), `message` is safe to show.
//
//   command  POST /api/v1/flows/{flow_id}/runs { revision_no: null, mode } -> 201 { run }  (flowgate.v02.0061 T#14:
//            the T#2 step / full command — a new run of the latest revision, or the Flow's idle run going on)
//   current  GET  /api/v1/flows/{flow_id}/runs/current                 -> { run | null }
//   pause    POST /api/v1/runs/{run_id}/pause  { reason? }              -> { run }  (T#2 Pause: running -> paused)
//   stop     POST /api/v1/runs/{run_id}/stop   { reason? }              -> { run }  (T#7 Stop: stopping, then idle)
//   resume   POST /api/v1/runs/{run_id}/resume { reason? }              -> { run }  (a paused run's own command)
//   approve  POST /api/v1/runs/{run_id}/nodes/{node_key}/approval { result_doc_id } -> { run, approval }
//            (flowgate.v02.0056 F2-1, O1 0005-TR §4-1: the card's [승인] — its documents not approved yet, the
//            result last; refusals are 409 / 403 / 404 / 422 codes, see center/graph/cardStateView.ts)
//   rewind   POST /api/v1/runs/{run_id}/nodes/{node_key}/time-machine (no body) -> { time_machine, run }
//            (flowgate.v02.0061 T#15: the T#13 Graph Time Machine of a done node and everything below it; 409
//            `graph_time_machine_blocked` carries the assessment — its `reasons` and `impact` — in the params)
//
// flowgate.v02.0056 F2-1: a run also carries `pending_propagation_count` / `pending_node_keys` (cards approved
// while it was stopped, their successors waiting) and each node `awaiting_approval` / `stage` (O1 / O2 §4-2).
//
// flowgate.v02.0061 T#14: a run also carries its command (`mode`), `idle_reason`, the running / requested execution
// counts with the Flow's `execution_limit`, and each node its attempt's `integration_status` and `hold`. T#15: and
// each node its current `attempt_id` (kept only when sent).
//
// The run events (P0008 §8.2 `graph_run_status_changed` / `graph_node_state_changed`) are refresh
// signals of the Canvas (flowCanvasEvents.ts), never state.
import { getRequest, postRequest } from '@shared/api'
import { canvasHeaders, num, str, type Raw } from './flowCanvasApi'
import { toGraphError } from './flowGraphApi'
import type { FlowRun, FlowRunApproval, FlowRunNode, FlowRunTimeMachine, FlowRunTimeMachineRefusal } from './types'

const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [])

function toRunNode(raw: Raw): FlowRunNode {
  const running = wholeCount(raw.running_execution_count)
  const requested = wholeCount(raw.requested_execution_count)
  return {
    nodeKey: String(raw.node_key ?? ''),
    status: String(raw.status ?? ''),
    resultDocId: str(raw.result_doc_id),
    ...(typeof raw.awaiting_approval === 'boolean' ? { awaitingApproval: raw.awaiting_approval } : {}),
    ...('stage' in raw ? { stage: str(raw.stage) } : {}),
    ...('integration_status' in raw ? { integrationStatus: str(raw.integration_status) } : {}),
    ...(typeof raw.hold === 'boolean' ? { hold: raw.hold } : {}),
    ...(running !== null ? { runningExecutionCount: running } : {}),
    ...(requested !== null ? { requestedExecutionCount: requested } : {}),
    ...('attempt_id' in raw ? { attemptId: str(raw.attempt_id) } : {}),
  }
}

/** A count the server sent as a whole number of zero or more; null otherwise. */
function wholeCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

const countOf = (value: unknown): number => wholeCount(value) ?? 0

/** The server's `run` object -> FlowRun, or null (no run). */
export function toFlowRun(raw: unknown): FlowRun | null {
  if (!raw || typeof raw !== 'object') return null
  const run = raw as Raw
  const runId = str(run.run_id)
  if (!runId) return null
  return {
    runId,
    flowId: String(run.flow_id ?? ''),
    revisionNo: num(run.revision_no),
    status: String(run.status ?? ''),
    mode: str(run.mode),
    idleReason: str(run.idle_reason),
    runningExecutionCount: countOf(run.running_execution_count),
    requestedExecutionCount: countOf(run.requested_execution_count),
    executionLimit: num(run.execution_limit),
    openNodeKeys: strings(run.open_node_keys),
    nodeRuns: Array.isArray(run.node_runs) ? run.node_runs.filter((n: unknown) => n && typeof n === 'object').map(toRunNode) : null,
    lastEventSeq: num(run.last_event_seq) ?? 0,
    pendingPropagationCount: Math.max(0, num(run.pending_propagation_count) ?? 0),
    pendingNodeKeys: strings(run.pending_node_keys),
  }
}

const flowRunsPath = (flowId: string) => `/api/v1/flows/${encodeURIComponent(flowId)}/runs`
const runPath = (runId: string) => `/api/v1/runs/${encodeURIComponent(runId)}`

/** GET /api/v1/flows/{flowId}/runs/current — the Flow's run (running / paused / stopping / idle), or null. */
export async function fetchCurrentFlowRun(flowId: string): Promise<FlowRun | null> {
  try {
    const response = await getRequest<Raw>(`${flowRunsPath(flowId)}/current`, {}, canvasHeaders())
    return toFlowRun(response.data?.run)
  } catch (error) {
    throw toGraphError(error, 'Failed to read the flow run.')
  }
}

function runOf(data: Raw | undefined, fallback: string): FlowRun {
  const run = toFlowRun(data?.run)
  if (!run) throw new Error(fallback)
  return run
}

/** The server's step / full command (flowgate.v02.0061 T#2). */
export type FlowRunCommand = 'step' | 'full'

/**
 * POST /api/v1/flows/{flowId}/runs — flowgate.v02.0061 T#14: one step / full command. The server starts a run of the
 * latest revision, or lets the Flow's idle run go on with the command; nothing else is called after it.
 */
export async function startFlowRun(flowId: string, mode: FlowRunCommand = 'full'): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(flowRunsPath(flowId), { revision_no: null, mode }, canvasHeaders())
    return runOf(response.data, 'The run did not start.')
  } catch (error) {
    throw toGraphError(error, 'Failed to start the flow run.')
  }
}

/** POST /api/v1/runs/{runId}/pause — flowgate.v02.0061 T#14: Pause (running -> paused, open nodes keep their work). */
export async function pauseFlowRun(runId: string, reason?: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/pause`, reason ? { reason } : {}, canvasHeaders())
    return runOf(response.data, 'The run did not pause.')
  } catch (error) {
    throw toGraphError(error, 'Failed to pause the flow run.')
  }
}

/**
 * POST /api/v1/runs/{runId}/stop — flowgate.v02.0061 T#7 Stop: the run is stopping at once and idle (stopped) once
 * its AI processes ended; its unfinished nodes go back to pending.
 */
export async function stopFlowRun(runId: string, reason?: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/stop`, reason ? { reason } : {}, canvasHeaders())
    return runOf(response.data, 'The run did not stop.')
  } catch (error) {
    throw toGraphError(error, 'Failed to stop the flow run.')
  }
}

/** POST /api/v1/runs/{runId}/resume — a paused run goes on with its own command (results bound meanwhile first). */
export async function resumeFlowRun(runId: string, reason?: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/resume`, reason ? { reason } : {}, canvasHeaders())
    return runOf(response.data, 'The run did not resume.')
  } catch (error) {
    throw toGraphError(error, 'Failed to resume the flow run.')
  }
}

/** flowgate.v02.0061 T#15: the server's `time_machine` object -> FlowRunTimeMachine. */
export function toFlowRunTimeMachine(raw: unknown, nodeKey: string): FlowRunTimeMachine {
  const body = raw && typeof raw === 'object' ? (raw as Raw) : {}
  const impact = body.impact && typeof body.impact === 'object' ? (body.impact as Raw) : {}
  return {
    operationId: str(body.operation_id),
    operationStatus: str(body.operation_status),
    nodeKey: str(body.node_key) ?? nodeKey,
    impactNodeKeys: strings(impact.node_keys),
    resetNodeKeys: strings(body.reset_node_keys),
    reopenedDocumentIds: strings(body.reopened_document_ids),
    revertedCommitCount: Array.isArray(body.reverts) ? body.reverts.length : 0,
    invalidatedAttemptCount: strings(body.invalidated_attempt_ids).length,
    revokedTokenCount: countOf(body.revoked_token_count),
    idleReason: str(body.idle_reason),
  }
}

/**
 * flowgate.v02.0061 T#15: the assessment a refused Graph Time Machine sends in its params (`reasons` with the
 * server's sentences, `impact.node_keys`), or null when the params carry no reason list.
 */
export function toFlowRunTimeMachineRefusal(params: unknown): FlowRunTimeMachineRefusal | null {
  const body = params && typeof params === 'object' ? (params as Raw) : {}
  if (!Array.isArray(body.reasons)) return null
  const impact = body.impact && typeof body.impact === 'object' ? (body.impact as Raw) : {}
  return {
    reasons: body.reasons
      .filter((r: unknown): r is Raw => !!r && typeof r === 'object')
      .map((r: Raw) => ({ code: String(r.code ?? ''), message: str(r.message) ?? '' })),
    impactNodeKeys: strings(impact.node_keys),
  }
}

/**
 * flowgate.v02.0061 T#15 — POST /api/v1/runs/{runId}/nodes/{nodeKey}/time-machine: the Graph Time Machine of a done
 * node (T#13): the node and every node below it go back to pending, their merges are reverted on the group branch, and
 * the run stays idle (nothing starts until the next command). Answers what it did and the run after it. A refusal
 * rejects with a FlowGraphApiError carrying the server's code / params / message; a `graph_time_machine_blocked` one
 * also carries the assessment as `timeMachine` (toFlowRunTimeMachineRefusal).
 */
export async function timeMachineFlowRunNode(runId: string, nodeKey: string): Promise<{ run: FlowRun; timeMachine: FlowRunTimeMachine }> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/nodes/${encodeURIComponent(nodeKey)}/time-machine`, {}, canvasHeaders())
    return {
      run: runOf(response.data, 'The Time Machine did not answer the run.'),
      timeMachine: toFlowRunTimeMachine(response.data?.time_machine, nodeKey),
    }
  } catch (error) {
    const refused = toGraphError(error, 'Failed to run the Graph Time Machine.')
    const assessment = toFlowRunTimeMachineRefusal(refused.params)
    if (assessment) Object.defineProperty(refused, 'timeMachine', { value: assessment, configurable: true, enumerable: true })
    throw refused
  }
}

/** The server's `approval` object -> FlowRunApproval. */
function toApproval(raw: unknown, nodeKey: string): FlowRunApproval {
  const approval = raw && typeof raw === 'object' ? (raw as Raw) : {}
  return {
    nodeKey: str(approval.node_key) ?? nodeKey,
    targetDocIds: strings(approval.target_doc_ids),
    approvedDocIds: strings(approval.approved_doc_ids),
  }
}

/**
 * flowgate.v02.0056 F2-1 — POST /api/v1/runs/{runId}/nodes/{nodeKey}/approval: approves the card (O1 0005-TR
 * §4-1). `resultDocId` is the result the screen showed (null: the server's current one); the server refuses
 * with 409 `result_changed` when the node's result moved meanwhile. Answers the run after it and what it
 * approved. A refusal rejects with a FlowGraphApiError carrying the server's code / params / message.
 */
export async function approveFlowRunNode(runId: string, nodeKey: string, resultDocId: string | null): Promise<{ run: FlowRun; approval: FlowRunApproval }> {
  try {
    const response = await postRequest<Raw>(
      `${runPath(runId)}/nodes/${encodeURIComponent(nodeKey)}/approval`,
      { result_doc_id: resultDocId },
      canvasHeaders(),
    )
    return { run: runOf(response.data, 'The card was not approved.'), approval: toApproval(response.data?.approval, nodeKey) }
  } catch (error) {
    throw toGraphError(error, 'Failed to approve the card.')
  }
}
