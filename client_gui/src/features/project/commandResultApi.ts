// flowgate.v02.0113 T#5 — the result of one step / full command (0030-M §5, D 개정 §3.14): what the [다음 단계] / [전체 실행]
// command did, read from the run's own records by the server. snake_case stays inside this file; the request carries this
// tab's `x-canvas-client-id` like the other run requests (flowRunApi.ts).
//
//   result  GET /api/v1/runs/{run_id}/command-result[?command_seq=N] -> { command_result }
//           (no command_seq: the run's latest command; 404 `command_not_found`, 422 a malformed command_seq)
import { getRequest } from '@shared/api'
import { canvasHeaders, num, str, type Raw } from './flowCanvasApi'
import { toGraphError } from './flowGraphApi'

/** How the command stands: still going, ended completed / failed / waiting, stopped, or taken over by another command. */
export type FlowCommandState = 'running' | 'completed' | 'failed' | 'waiting' | 'stopped' | 'superseded'

/**
 * Whose output a message's body and error are: the launch's own (`launch`), the AI run's final output shown on its last
 * launch for a run recorded before launch outputs were kept (`run`), none kept for this launch (`not_preserved`), or no
 * output at all (`none`: a launch still running, an admission refusal, an event).
 */
export type FlowCommandBodySource = 'launch' | 'run' | 'not_preserved' | 'none'

export interface FlowCommandNode {
  readonly nodeKey: string
  readonly title: string | null
  readonly docType: string | null
  readonly status: string | null
  /** done / failed / waiting / running / pending — as the command left the card. */
  readonly outcome: string
  readonly stage: string | null
  readonly reason: string | null
  readonly detail: string | null
}

export interface FlowCommandFailure {
  readonly nodeKey: string | null
  readonly title: string | null
  readonly code: string
  readonly detail: string | null
  readonly at: string | null
  readonly role: string | null
}

export interface FlowCommandRoleUse {
  /** ai / person / no_review / wait_setup / wait_unavailable */
  readonly kind: string
  readonly aiId: string | null
  readonly aiName: string | null
}

export interface FlowCommandModel {
  readonly aiId: string | null
  readonly aiName: string | null
  readonly state: string | null
  /** The requested model, or the confirmed models (told apart, §3.10). */
  readonly models: readonly string[]
  readonly reason: string | null
}

export interface FlowCommandMessage {
  /** invocation: a §3.10 role invocation record; event: a node event or a failure of the command. */
  readonly kind: 'invocation' | 'event'
  readonly key: string
  readonly event: string | null
  readonly code: string | null
  readonly nodeKey: string | null
  readonly title: string | null
  readonly role: string | null
  readonly purpose: string | null
  readonly at: string | null
  readonly from: string | null
  readonly to: string | null
  readonly detail: string | null
  readonly requested: FlowCommandModel | null
  readonly confirmed: FlowCommandModel | null
  readonly result: string | null
  readonly body: string
  readonly bodySource: FlowCommandBodySource
  /** How the record was told to this command: by the run event its request was made at, or by its time. */
  readonly attribution: 'event' | 'time' | null
  readonly launchNo: number | null
  readonly errorText: string | null
  readonly error: boolean
}

export interface FlowCommandResult {
  readonly runId: string
  readonly flowId: string
  readonly commandSeq: number
  readonly latest: boolean
  readonly scope: 'step' | 'full'
  readonly startedAt: string | null
  readonly endedAt: string | null
  readonly ended: boolean
  /** It ended waiting and may still go on with no new command (a person's save or approval): keep watching it. */
  readonly continues: boolean
  readonly state: FlowCommandState
  readonly idleReason: string | null
  readonly nextState: string | null
  readonly counts: { readonly done: number; readonly failed: number; readonly waiting: number }
  readonly nodes: readonly FlowCommandNode[]
  readonly failure: FlowCommandFailure | null
  readonly roles: {
    readonly instruction: readonly FlowCommandRoleUse[]
    readonly execution: readonly FlowCommandRoleUse[]
    readonly review: readonly FlowCommandRoleUse[]
  }
  readonly messages: readonly FlowCommandMessage[]
}

const STATES: readonly FlowCommandState[] = ['running', 'completed', 'failed', 'waiting', 'stopped', 'superseded']
const BODY_SOURCES: readonly FlowCommandBodySource[] = ['launch', 'run', 'not_preserved', 'none']
const objects = (value: unknown): Raw[] => (Array.isArray(value) ? value.filter((v): v is Raw => !!v && typeof v === 'object') : [])
const count = (value: unknown): number => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0)

function toFailure(raw: unknown): FlowCommandFailure | null {
  if (!raw || typeof raw !== 'object') return null
  const f = raw as Raw
  return { nodeKey: str(f.node_key), title: str(f.title), code: String(f.code ?? ''), detail: str(f.detail), at: str(f.at), role: str(f.role) }
}

function toRoleUses(value: unknown): FlowCommandRoleUse[] {
  return objects(value).map((r) => ({ kind: String(r.kind ?? ''), aiId: str(r.ai_id), aiName: str(r.ai_name) }))
}

function toModel(raw: unknown, many: boolean): FlowCommandModel | null {
  if (!raw || typeof raw !== 'object') return null
  const m = raw as Raw
  const models = many
    ? (Array.isArray(m.models) ? m.models.filter((v: unknown): v is string => typeof v === 'string') : [])
    : (typeof m.model === 'string' ? [m.model] : [])
  return { aiId: str(m.ai_id), aiName: str(m.ai_name), state: str(m.state), models, reason: str(m.reason) }
}

function toMessage(raw: Raw, index: number): FlowCommandMessage {
  const kind = raw.kind === 'invocation' ? 'invocation' : 'event'
  return {
    kind,
    key: `${index}:${str(raw.inv_id) ?? str(raw.code) ?? ''}`,
    event: str(raw.event),
    code: str(raw.code),
    nodeKey: str(raw.node_key),
    title: str(raw.title),
    role: str(raw.role),
    purpose: str(raw.purpose),
    at: str(raw.at),
    from: str(raw.from),
    to: str(raw.to),
    detail: str(raw.detail),
    requested: kind === 'invocation' ? toModel(raw.requested, false) : null,
    confirmed: kind === 'invocation' ? toModel(raw.confirmed, true) : null,
    result: str(raw.result),
    body: typeof raw.body === 'string' ? raw.body : '',
    bodySource: BODY_SOURCES.includes(raw.body_source as FlowCommandBodySource) ? (raw.body_source as FlowCommandBodySource) : 'none',
    attribution: raw.attribution === 'event' || raw.attribution === 'time' ? raw.attribution : null,
    launchNo: num(raw.launch_no),
    errorText: str(raw.error_text),
    error: raw.error === true,
  }
}

/** The server's `command_result` object -> FlowCommandResult. */
export function toFlowCommandResult(raw: unknown): FlowCommandResult {
  const r = raw && typeof raw === 'object' ? (raw as Raw) : {}
  const counts = r.counts && typeof r.counts === 'object' ? (r.counts as Raw) : {}
  const roles = r.roles && typeof r.roles === 'object' ? (r.roles as Raw) : {}
  const state = STATES.includes(r.state as FlowCommandState) ? (r.state as FlowCommandState) : 'waiting'
  return {
    runId: String(r.run_id ?? ''),
    flowId: String(r.flow_id ?? ''),
    commandSeq: num(r.command_seq) ?? 0,
    latest: r.latest === true,
    scope: r.scope === 'step' ? 'step' : 'full',
    startedAt: str(r.started_at),
    endedAt: str(r.ended_at),
    ended: r.ended === true,
    continues: r.continues === true,
    state,
    idleReason: str(r.idle_reason),
    nextState: str(r.next_state),
    counts: { done: count(counts.done), failed: count(counts.failed), waiting: count(counts.waiting) },
    nodes: objects(r.nodes).map((n) => ({
      nodeKey: String(n.node_key ?? ''), title: str(n.title), docType: str(n.doc_type), status: str(n.status),
      outcome: String(n.outcome ?? ''), stage: str(n.stage), reason: str(n.reason), detail: str(n.detail),
    })),
    failure: toFailure(r.failure),
    roles: { instruction: toRoleUses(roles.instruction), execution: toRoleUses(roles.execution), review: toRoleUses(roles.review) },
    messages: objects(r.messages).map(toMessage),
  }
}

/** GET /api/v1/runs/{runId}/command-result — the run's latest command (or the one starting at `commandSeq`). */
export async function fetchFlowCommandResult(runId: string, commandSeq?: number): Promise<FlowCommandResult> {
  const query = commandSeq !== undefined ? `?command_seq=${encodeURIComponent(String(commandSeq))}` : ''
  try {
    const response = await getRequest<Raw>(`/api/v1/runs/${encodeURIComponent(runId)}/command-result${query}`, {}, canvasHeaders())
    return toFlowCommandResult(response.data?.command_result)
  } catch (error) {
    throw toGraphError(error, 'Failed to read the command result.')
  }
}
