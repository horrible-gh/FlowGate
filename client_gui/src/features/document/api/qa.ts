// flowgate.v02.0048 T#3 — skeleton of the qa document API (D#1 §6.3). Owned by T#7 (qna), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `qa`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   list     GET  /api/v1/q/{doc_id}
//   answer   POST /api/v1/q/{doc_id}/items/{item_id}/answers  { body, author_kind, selected_option_ids }
//
// flowgate.v02.0048 T#7 — the Inspector Q&A tab's calls, both existing v0.1 endpoints (no new server
// API):
//   list     GET  /api/v1/q/{doc_id} -> { ok, qa: { doc_id, status, items: [{ id, seq, title, body,
//            asker_kind, answer_count, options: [{ id, label }], asker_provider, answers: [{ id, body,
//            author_kind, author_id, selected_options, author_provider, created_at }] }] } }
//            A document without a question container answers `{ status: null, items: [] }`.
//   answer   POST /api/v1/q/{doc_id}/items/{item_id}/answers { body, author_kind: 'human',
//            selected_option_ids } -> { ok, doc_id, item_id, answer_id, author_kind, status }
//            An answer picks one option, writes prose, or both; a pick alone is stored with the
//            option's label as its body (server side).
// The AI answer hand-off (`answers/ai-request`, `answers/ai-mention`) and adding a question are not
// wrapped (D#1 §10.2 T#7 row).
//
// flowgate.v02.0053 T#6 (0053 CH §2) — a person asks on the Q&A tab and an AI answers, so two more
// existing v0.1 endpoints are wrapped (still no new server API):
//   ask      POST /api/v1/q/{doc_id}/questions { asker_kind: 'human', questions: [{ body }] }
//            -> { ok, doc_id, added_item_ids: [id] }
//   ai       POST /api/v1/q/{doc_id}/items/{item_id}/answers/ai-request {} -> { ok, doc_id, item_id,
//            run_id, status, provider }; the AI run posts its answer on the same question
//            (author_kind 'ai'), which arrives as `qna_answer_registered`. Its admission refusals keep
//            the ai-invoke envelope: a flat `code` (409 run_in_progress, no_enabled_provider …).
//   `answers/ai-mention` (copy a mention for one's own worker) stays unwrapped.
//
// Refusals keep the server's shape and are thrown as they are; qaErrorOf reads them. The q routes
// answer `{ ok: false, http_status, error_message }` (409 = the group was disposed, 403, 404, 400);
// the group mutation policy answers 423 `{ error: { code: 'GROUP_AI_RUN_LOCKED' } }` while an AI run
// owns the group.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const integer = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null)

/** A reference option of a question; the answerer may pick one instead of writing. */
export interface QaOption {
  readonly id: string
  readonly label: string
}

/** The AI run / provider that asked or answered, when the server knows it. */
export interface QaProvider {
  readonly runId: string | null
  readonly providerId: string | null
  readonly providerName: string | null
}

export interface QaAnswer {
  readonly id: number | null
  readonly body: string
  /** 'human' or 'ai' as the server sent it. */
  readonly authorKind: string
  readonly authorId: string | null
  /** Ids of the picked options (at most one today). */
  readonly selectedOptionIds: readonly string[]
  readonly provider: QaProvider | null
  readonly createdAt: string | null
}

export interface QaItem {
  readonly id: number
  readonly seq: number | null
  readonly title: string | null
  readonly body: string
  /** 'ai' (an AI asked the user) or 'human' (a person asked) as the server sent it. */
  readonly askerKind: string
  readonly options: readonly QaOption[]
  /** The server's answer counter (answers.length may be read as well). */
  readonly answerCount: number
  readonly answers: readonly QaAnswer[]
  readonly provider: QaProvider | null
  readonly createdAt: string | null
}

/** The questions of one document. */
export interface QaThread {
  readonly docId: string
  /** The container status ('pending' / 'done'), null without a container. */
  readonly status: string | null
  readonly items: readonly QaItem[]
}

function toQaProvider(raw: unknown): QaProvider | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Raw
  const provider = { runId: text(row.ai_run_id), providerId: text(row.ai_provider_id), providerName: text(row.ai_provider_name) }
  return provider.runId || provider.providerId || provider.providerName ? provider : null
}

function toQaOption(raw: unknown): QaOption | null {
  const row = record(raw)
  const id = text(row.id)
  return id === null ? null : { id, label: typeof row.label === 'string' ? row.label : '' }
}

function toQaAnswer(raw: unknown): QaAnswer {
  const row = record(raw)
  return {
    id: integer(row.id),
    body: typeof row.body === 'string' ? row.body : '',
    authorKind: text(row.author_kind) ?? '',
    authorId: text(row.author_id),
    selectedOptionIds: list(row.selected_options).filter((id): id is string => typeof id === 'string'),
    provider: toQaProvider(row.author_provider),
    createdAt: text(row.created_at),
  }
}

/** One question item; null when it has no id (it could not be answered). */
function toQaItem(raw: unknown): QaItem | null {
  const row = record(raw)
  const id = integer(row.id)
  if (id === null) return null
  return {
    id,
    seq: integer(row.seq),
    title: text(row.title),
    body: typeof row.body === 'string' ? row.body : '',
    askerKind: text(row.asker_kind) ?? '',
    options: list(row.options).flatMap((option) => toQaOption(option) ?? []),
    answerCount: integer(row.answer_count) ?? 0,
    answers: list(row.answers).map(toQaAnswer),
    provider: toQaProvider(row.asker_provider),
    createdAt: text(row.created_at),
  }
}

/** The `GET /q/{doc_id}` response -> QaThread. Throws when it carries no Q&A of a document. */
export function toQaThread(raw: unknown): QaThread {
  const qa = record(record(raw).qa)
  const docId = text(qa.doc_id) ?? text(qa.q_id)
  if (docId === null) throw new Error('Q&A response without doc_id')
  return { docId, status: text(qa.status), items: list(qa.items).flatMap((item) => toQaItem(item) ?? []) }
}

/** Reads the questions of a document. Transport errors are thrown as they are. */
export async function qaRead(docId: string): Promise<QaThread> {
  const response = await getRequest<unknown>(`${API}/q/${encodeURIComponent(docId)}`)
  return toQaThread(response.data)
}

export interface QaAnswerRequest {
  readonly docId: string
  readonly itemId: number
  /** The written answer (may be empty when an option is picked). */
  readonly body: string
  readonly selectedOptionIds: readonly string[]
}

export interface QaAnswerResult {
  readonly docId: string | null
  readonly itemId: number | null
  readonly answerId: number | null
  readonly authorKind: string | null
  /** The container status after the answer ('done' when every question has an answer). */
  readonly status: string | null
}

/** The answer response -> QaAnswerResult. */
export function toQaAnswerResult(raw: unknown): QaAnswerResult {
  const row = record(raw)
  return {
    docId: text(row.doc_id),
    itemId: integer(row.item_id),
    answerId: integer(row.answer_id),
    authorKind: text(row.author_kind),
    status: text(row.status),
  }
}

/** Answers one question as the signed-in person (author_kind 'human'). */
export async function qaAnswer(request: QaAnswerRequest): Promise<QaAnswerResult> {
  const response = await postRequest<unknown>(
    `${API}/q/${encodeURIComponent(request.docId)}/items/${encodeURIComponent(String(request.itemId))}/answers`,
    { body: request.body, author_kind: 'human', selected_option_ids: [...request.selectedOptionIds] },
  )
  return toQaAnswerResult(response.data)
}

export interface QaAskRequest {
  readonly docId: string
  /** The question as written (sent trimmed by the caller); its first line is the list title. */
  readonly body: string
}

export interface QaAskResult {
  readonly docId: string | null
  /** The ids of the questions the server added (one per question sent). */
  readonly itemIds: readonly number[]
}

/** The add-questions response -> QaAskResult. */
export function toQaAskResult(raw: unknown): QaAskResult {
  const row = record(raw)
  return { docId: text(row.doc_id), itemIds: list(row.added_item_ids).flatMap((id) => integer(id) ?? []) }
}

/** Registers one question on the document as the signed-in person (asker_kind 'human'). */
export async function qaAsk(request: QaAskRequest): Promise<QaAskResult> {
  const response = await postRequest<unknown>(`${API}/q/${encodeURIComponent(request.docId)}/questions`, {
    asker_kind: 'human',
    questions: [{ body: request.body }],
  })
  return toQaAskResult(response.data)
}

export interface QaAiRequestResult {
  readonly docId: string | null
  readonly itemId: number | null
  /** The AI run that will answer, when the server started one. */
  readonly runId: string | null
  readonly status: string | null
}

/** The ai-request response -> QaAiRequestResult. */
export function toQaAiRequestResult(raw: unknown): QaAiRequestResult {
  const row = record(raw)
  return { docId: text(row.doc_id), itemId: integer(row.item_id), runId: text(row.run_id), status: text(row.status) }
}

/** Asks an AI to answer one question (the project's provider chain; the answer lands on the question). */
export async function qaRequestAiAnswer(docId: string, itemId: number): Promise<QaAiRequestResult> {
  const response = await postRequest<unknown>(
    `${API}/q/${encodeURIComponent(docId)}/items/${encodeURIComponent(String(itemId))}/answers/ai-request`,
    {},
  )
  return toQaAiRequestResult(response.data)
}

/**
 * Why a Q&A call was refused:
 * running = an AI run owns the group (423 GROUP_AI_RUN_LOCKED, or the ai-request admission's 409
 * run_in_progress / run_still_live); no_provider = no AI provider can take the ai-request
 * (no_enabled_provider, no_provider_registered, provider_unavailable / disabled); disposed = the
 * group was disposed (a 409 without a code, the only one of the q routes); forbidden 403; not_found
 * 404 (the document or the question is gone); invalid 400 / 422; network = no answer.
 */
export type QaErrorKind = 'running' | 'no_provider' | 'disposed' | 'forbidden' | 'not_found' | 'invalid' | 'network' | 'other'

const QA_RUNNING_CODES = new Set(['GROUP_AI_RUN_LOCKED', 'run_in_progress', 'run_still_live'])
const QA_PROVIDER_CODES = new Set(['no_enabled_provider', 'no_provider_registered', 'provider_unavailable', 'provider_disabled'])

export interface QaError {
  readonly kind: QaErrorKind
  readonly status: number | null
  readonly code: string | null
}

/** What a failed Q&A call threw, as kind / status / code. Only code and status are read. */
export function qaErrorOf(error: unknown): QaError {
  const normalized = normalizeApiError(error)
  // The q routes put the status in the body as `http_status`, the transport on the response.
  const data = record(record(record(error).response).data)
  const status = normalized.status ?? integer(data.http_status)
  // The ai-request admission answers a flat `{ code }` beside `http_status` (the ai-invoke envelope).
  const code = normalized.code ?? text(data.code)
  let kind: QaErrorKind
  if (normalized.network) kind = 'network'
  else if (status === 423 || (code !== null && QA_RUNNING_CODES.has(code))) kind = 'running'
  else if (code !== null && QA_PROVIDER_CODES.has(code)) kind = 'no_provider'
  else if (status === 409) kind = code === null || code === 'group_disposed' ? 'disposed' : 'other'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}
