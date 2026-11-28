// flowgate.v02test.0053 1-D — a work card's `검증` line (mockup v15, http://mirageglass.stg/v/qxzmvtjk/v15/selfcheck.html):
// the card's verification (self-check) run that went past its time, or this card's request waiting behind such a run.
// User JWT only, the Graph envelope; every call needs the card Flow's write permission.
//
//   read          GET  /api/v1/flows/{flow_id}/nodes/{node_key}/self-check                         -> { verification }
//   force stop    POST /api/v1/flows/{flow_id}/nodes/{node_key}/self-check/runs/{run_id}/force-stop      -> { verification }
//   cancel wait   POST /api/v1/flows/{flow_id}/nodes/{node_key}/self-check/runs/{run_id}/cancel-waiting  -> { verification }
//
//   verification = { flow_id, node_key, visible, state: hidden | overdue | waiting | stopped | cancelled | stop_failed,
//                    run: { self_check_run_id, status, started_at, enqueued_at, finished_at, show_at, elapsed_seconds } | null,
//                    blocker: run + { flow_id, node_key, label, project_id } | null,
//                    actions: [force_stop | cancel_waiting | mark_ended],
//                    stop_reason: host_changed | processes_remain | owner_alive | <other> | absent (stop_failed only) }
//   mark ended    POST /api/v1/flows/{flow_id}/nodes/{node_key}/self-check/runs/{run_id}/mark-ended      -> { verification }
//
// flowgate.v02test.0084 #13: `stop_reason` says why a stop could not be confirmed and `mark_ended` (stop_failed only)
// records the run as ended by a person. Both are optional: an older server sends neither and the line works as before.
// `stopped` / `cancelled` come from an action only (the next read hides them). Refusals are the Graph envelope:
// 409 selfcheck_not_waiting (the request started: read again) / selfcheck_not_running / selfcheck_not_supported,
// 404 selfcheck_run_not_found / node_not_found, 403 flow_access_denied.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : null)

export const cardVerificationStates = ['hidden', 'overdue', 'waiting', 'stopped', 'cancelled', 'stop_failed'] as const
export type CardVerificationState = (typeof cardVerificationStates)[number]
export const cardVerificationActions = ['force_stop', 'cancel_waiting', 'mark_ended'] as const
export type CardVerificationAction = (typeof cardVerificationActions)[number]

export interface CardVerificationRun {
  readonly runId: string
  readonly status: string | null
  readonly startedAt: string | null
  readonly enqueuedAt: string | null
  readonly finishedAt: string | null
  /** When the line appears for this run (last launch + run time limit + 1 minute); null before it starts. */
  readonly showAt: string | null
  readonly elapsedSeconds: number | null
}

/** The overdue run another card's request waits behind (only when the reader may see that card). */
export interface CardVerificationBlocker extends CardVerificationRun {
  readonly flowId: string
  readonly nodeKey: string
  readonly label: string
  readonly projectId: string | null
}

export interface CardVerification {
  readonly flowId: string
  readonly nodeKey: string
  readonly visible: boolean
  readonly state: CardVerificationState
  readonly run: CardVerificationRun | null
  readonly blocker: CardVerificationBlocker | null
  readonly actions: readonly CardVerificationAction[]
  /** Why the last stop could not be confirmed (the server's code as sent), or null when the server says nothing. */
  readonly stopReason: string | null
}

function run(raw: unknown): CardVerificationRun | null {
  const row = record(raw)
  const runId = text(row.self_check_run_id)
  if (!runId) return null
  return {
    runId,
    status: text(row.status),
    startedAt: text(row.started_at),
    enqueuedAt: text(row.enqueued_at),
    finishedAt: text(row.finished_at),
    showAt: text(row.show_at),
    elapsedSeconds: count(row.elapsed_seconds),
  }
}

function blocker(raw: unknown): CardVerificationBlocker | null {
  const row = record(raw)
  const base = run(row)
  const flowId = text(row.flow_id)
  const nodeKey = text(row.node_key)
  if (!base || !flowId || !nodeKey) return null
  return { ...base, flowId, nodeKey, label: text(row.label) ?? nodeKey, projectId: text(row.project_id) }
}

/** An answer's `verification` -> CardVerification (unknown state or `visible: false` reads as hidden). */
export function toCardVerification(raw: unknown, flowId = '', nodeKey = ''): CardVerification {
  const row = record(raw)
  const known = cardVerificationStates.find((key) => key === row.state) ?? 'hidden'
  const visible = row.visible === true && known !== 'hidden'
  return {
    flowId: text(row.flow_id) ?? flowId,
    nodeKey: text(row.node_key) ?? nodeKey,
    visible,
    state: visible ? known : 'hidden',
    run: run(row.run),
    blocker: blocker(row.blocker),
    actions: Array.isArray(row.actions)
      ? cardVerificationActions.filter((action) => (row.actions as unknown[]).includes(action))
      : [],
    stopReason: text(row.stop_reason) ?? text(record(row.run).stop_reason),
  }
}

const verificationPath = (flowId: string, nodeKey: string) =>
  `${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/self-check`

const ACTION_PATH: Record<CardVerificationAction, string> = {
  force_stop: 'force-stop',
  cancel_waiting: 'cancel-waiting',
  mark_ended: 'mark-ended',
}

/** Reads a card's verification line. */
export async function cardVerificationRead(flowId: string, nodeKey: string): Promise<CardVerification> {
  const response = await getRequest<unknown>(verificationPath(flowId, nodeKey))
  return toCardVerification(record(response.data).verification, flowId, nodeKey)
}

/** Presses [강제 종료] / [취소] / [끝난 것으로 처리] for the exact run the line was drawn for; answers the line after it. */
export async function cardVerificationAct(
  flowId: string,
  nodeKey: string,
  runId: string,
  action: CardVerificationAction,
): Promise<CardVerification> {
  const path = `${verificationPath(flowId, nodeKey)}/runs/${encodeURIComponent(runId)}/${ACTION_PATH[action]}`
  const response = await postRequest<unknown>(path, {})
  return toCardVerification(record(response.data).verification, flowId, nodeKey)
}

/** A refused verification call as the line reads it. */
export interface CardVerificationError {
  readonly code: string | null
  readonly status: number | null
  readonly network: boolean
}

export function cardVerificationErrorOf(error: unknown): CardVerificationError {
  const normalized = normalizeApiError(error)
  return { code: normalized.code, status: normalized.status, network: normalized.network }
}
