// flowgate.v02.0056 F2-2 — a single AI call on one document (O3 flowgate.v02.0056.0009-TR §4-3, the v0.1 route
// reused): the user picks the AI, writes an opinion and the AI works on the document once (`edit`). No review
// loop is sent (`document_review_loop` stays out), so no review · rework round and no card approval follow the
// run — even when the document is a card's result.
//
//   start   POST /api/v1/ai-invoke/start  { project, module, group, doc_ref, action_scope: 'edit', mode: 'single',
//                                           provider_id, provider_pinned: true, messages: [<opinion>] }
//           -> the v0.1 start result as it is ({ run_id, status, … }). The server puts the opinion before the
//           mention under "사용자 메세지" (x-locale), then the card's AI memos.
//   run     GET  /api/v1/ai-invoke/{run_id}            -> { status, end_reason, … } (status 'finished' ends it)
//   active  GET  /api/v1/ai-invoke/active?group_id=    -> { active, run_id, status, doc_ref, … }
//
// These are the v0.1 envelope: success is the body itself, a refusal a flat `{ code, message, … }` (409
// run_in_progress with the `run_id` holding the group, 422 validation_failed, 403 permission_denied /
// user_session_required, 404 project_not_found, the v0.1 admission codes) — documentAiErrorOf reads both that
// and an `error` / `detail` object.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'
import { reviewGroupParts } from './review'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

export interface DocumentAiStartRequest {
  /** The Flow = v0.1 group id the document belongs to. */
  readonly groupId: string
  readonly docId: string
  /** The AI the user picked (pinned: the server takes it as it is). */
  readonly providerId: string
  /** The user's opinions; blank ones are left out. */
  readonly messages: readonly string[]
}

/** The start request body (no `document_review_loop`: nothing follows the run). Throws on a non-group id. */
export function documentAiStartPayload(request: DocumentAiStartRequest): Raw {
  const parts = reviewGroupParts(request.groupId)
  if (!parts) throw new Error(`not a group id: ${request.groupId}`)
  return {
    project: parts.project,
    module: parts.module,
    group: parts.group,
    doc_ref: request.docId,
    action_scope: 'edit',
    mode: 'single',
    provider_id: request.providerId,
    provider_pinned: true,
    messages: request.messages.map((message) => message.trim()).filter((message) => message !== ''),
  }
}

/** A run as the screen follows it. */
export interface DocumentAiRunState {
  readonly runId: string
  readonly status: string | null
  /** Why a finished run ended (`completed`, `cancelled`, `failed` …), as sent. */
  readonly endReason: string | null
  /** The run is over (status `finished`). */
  readonly finished: boolean
}

/** Starts the single AI call; answers the run it started. */
export async function documentAiStart(request: DocumentAiStartRequest): Promise<DocumentAiRunState> {
  const response = await postRequest<unknown>(`${API}/ai-invoke/start`, documentAiStartPayload(request))
  const row = record(response.data)
  const runId = text(row.run_id)
  if (!runId) throw new Error('ai-invoke start answered without a run id')
  const status = text(row.status)
  return { runId, status, endReason: text(row.end_reason), finished: status === 'finished' }
}

/** The run read -> DocumentAiRunState. */
export function toDocumentAiRunState(runId: string, raw: unknown): DocumentAiRunState {
  const row = record(raw)
  const status = text(row.status)
  return { runId, status, endReason: text(row.end_reason), finished: status === 'finished' }
}

/** Reads one run's state. */
export async function documentAiRunRead(runId: string): Promise<DocumentAiRunState> {
  const response = await getRequest<unknown>(`${API}/ai-invoke/${encodeURIComponent(runId)}`)
  return toDocumentAiRunState(runId, response.data)
}

/** The group's live AI run, if any (the group lease: one run at a time). */
export interface DocumentAiActivity {
  readonly active: boolean
  readonly runId: string | null
  readonly status: string | null
  /** The document the run works on. */
  readonly docRef: string | null
}

/** The /ai-invoke/active answer -> DocumentAiActivity (a finished run is not active). */
export function toDocumentAiActivity(raw: unknown): DocumentAiActivity {
  const row = record(raw)
  const status = text(row.status)
  return { active: row.active === true && status !== 'finished', runId: text(row.run_id), status, docRef: text(row.doc_ref) }
}

/** Reads whether an AI run owns the group now. */
export async function documentAiActiveRead(groupId: string): Promise<DocumentAiActivity> {
  const response = await getRequest<unknown>(`${API}/ai-invoke/active`, { group_id: groupId })
  return toDocumentAiActivity(response.data)
}

/** A refused start as the screen reads it. */
export interface DocumentAiError {
  readonly code: string | null
  readonly status: number | null
  /** No answer reached the screen. */
  readonly network: boolean
  /** The server's message as it sent it (x-locale), or ''. Shown as it is (409 run_in_progress). */
  readonly message: string
  /** 409 run_in_progress: the run holding the group. */
  readonly runId: string | null
}

/** What a failed documentAiStart threw: the flat v0.1 `{ code, message }`, or an `error` / `detail` object. */
export function documentAiErrorOf(error: unknown): DocumentAiError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  const source = Object.keys(normalized.source).length ? (normalized.source as Raw) : data
  const message = typeof source.message === 'string' ? source.message.trim() : typeof data.message === 'string' ? data.message.trim() : ''
  return {
    code: normalized.code ?? (text(data.code)?.trim() || null),
    status: normalized.status,
    network: normalized.network,
    message,
    runId: text(source.run_id) ?? text(data.run_id),
  }
}
