// flowgate.v02.0053 T#9 — Graph run transport (P0008 §7 S5-1 ~ S5-5): start a Flow's run, read its
// current run, stop and resume a run. snake_case stays inside this file; every request carries this tab's
// `x-canvas-client-id` (flowCanvasApi.ts) like the Graph saver's. A refused request rejects with a
// FlowGraphApiError (flowGraphApi.ts): `code` / `params` are the server's (`run_policy_undecided`,
// `run_already_active`, `run_not_running`, `flow_owned_by_v01`, …), `message` is safe to show.
//
//   start    POST /api/v1/flows/{flow_id}/runs { revision_no: null }  -> 201 { run }  (latest revision)
//   current  GET  /api/v1/flows/{flow_id}/runs/current                 -> { run | null } (running / stopped)
//   stop     POST /api/v1/runs/{run_id}/stop   { reason? }              -> { run }
//   resume   POST /api/v1/runs/{run_id}/resume { reason? }              -> { run }
//
// The run events (P0008 §8.2 `graph_run_status_changed` / `graph_node_state_changed`) are refresh
// signals of the Canvas (flowCanvasEvents.ts), never state.
import { getRequest, postRequest } from '@shared/api'
import { canvasHeaders, num, str, type Raw } from './flowCanvasApi'
import { toGraphError } from './flowGraphApi'
import type { FlowRun, FlowRunNode } from './types'

const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [])

function toRunNode(raw: Raw): FlowRunNode {
  return { nodeKey: String(raw.node_key ?? ''), status: String(raw.status ?? ''), resultDocId: str(raw.result_doc_id) }
}

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
    openNodeKeys: strings(run.open_node_keys),
    nodeRuns: Array.isArray(run.node_runs) ? run.node_runs.filter((n: unknown) => n && typeof n === 'object').map(toRunNode) : null,
    lastEventSeq: num(run.last_event_seq) ?? 0,
  }
}

const flowRunsPath = (flowId: string) => `/api/v1/flows/${encodeURIComponent(flowId)}/runs`
const runPath = (runId: string) => `/api/v1/runs/${encodeURIComponent(runId)}`

/** GET /api/v1/flows/{flowId}/runs/current — the Flow's running or stopped run, or null. */
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

/** POST /api/v1/flows/{flowId}/runs — starts a run of the latest revision. */
export async function startFlowRun(flowId: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(flowRunsPath(flowId), { revision_no: null }, canvasHeaders())
    return runOf(response.data, 'The run did not start.')
  } catch (error) {
    throw toGraphError(error, 'Failed to start the flow run.')
  }
}

/** POST /api/v1/runs/{runId}/stop — open nodes keep their work; nothing new opens until resumed. */
export async function stopFlowRun(runId: string, reason?: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/stop`, reason ? { reason } : {}, canvasHeaders())
    return runOf(response.data, 'The run did not stop.')
  } catch (error) {
    throw toGraphError(error, 'Failed to stop the flow run.')
  }
}

/** POST /api/v1/runs/{runId}/resume — a stopped run goes on (results bound meanwhile are taken first). */
export async function resumeFlowRun(runId: string, reason?: string): Promise<FlowRun> {
  try {
    const response = await postRequest<Raw>(`${runPath(runId)}/resume`, reason ? { reason } : {}, canvasHeaders())
    return runOf(response.data, 'The run did not resume.')
  } catch (error) {
    throw toGraphError(error, 'Failed to resume the flow run.')
  }
}
