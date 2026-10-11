// flowgate.v02.0053 T#9 — the Graph run as the Inspector's 확인 card acts on it (P0008 §7 S5-4 / S6-1). No
// new server API: the Graph run routes (server graph_workflow/api/run_routes.py).
//
//   current   GET  /api/v1/flows/{flow_id}/runs/current -> { run: { run_id, status, node_runs[] } | null }
//   decide    POST /api/v1/runs/{run_id}/nodes/{node_key}/gate-decision { decision, comment? } -> { run }
//
// `decision` is `confirm` | `reject`; the comment (a reject's reason) is at most 500 characters. A refusal
// is thrown as it is (see documentErrorOf): 409 `run_not_running` (a stopped run takes no decision),
// `node_not_open`, `node_not_gate`, `run_policy_undecided` (the gate rule is not decided yet) …
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

/** The longest comment the server takes (run_request.RUN_REASON_MAX_LEN). */
export const RUN_COMMENT_MAX_LENGTH = 500

export type RunGateDecision = 'confirm' | 'reject'

export interface RunNodeState {
  readonly nodeKey: string
  /** `pending` | `open` | `review` | `done` | `rejected` … as sent. */
  readonly status: string
}

export interface RunCurrent {
  readonly runId: string
  /** `running` | `stopped` | `completed` as sent. */
  readonly status: string
  readonly nodes: readonly RunNodeState[]
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
    nodes: rows
      .filter((row): row is Raw => !!row && typeof row === 'object' && typeof (row as Raw).node_key === 'string')
      .map((row) => ({ nodeKey: row.node_key as string, status: typeof row.status === 'string' ? row.status : '' })),
  }
}

/** The Flow's running or stopped run with its nodes, or null. */
export async function runCurrentRead(flowId: string): Promise<RunCurrent | null> {
  const response = await getRequest<Raw>(`${API}/flows/${encodeURIComponent(flowId)}/runs/current`)
  return toRunCurrent(response.data?.run)
}

/** Confirms or rejects an open gate node of a running run; answers the run after it. */
export async function runGateDecide(runId: string, nodeKey: string, decision: RunGateDecision, comment?: string): Promise<RunCurrent | null> {
  const text = comment?.trim() ?? ''
  const response = await postRequest<Raw>(
    `${API}/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeKey)}/gate-decision`,
    text ? { decision, comment: text.slice(0, RUN_COMMENT_MAX_LENGTH) } : { decision },
  )
  return toRunCurrent(response.data?.run)
}
