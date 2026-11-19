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
// flowgate.v02.0064 T-B5 (P0006 §5.1·§6.4) — every answer carries `author_name`: the person author's
// name (`users.username`), null for an AI answer or a person the server could not find.
//
// flowgate.v02.0053 T#6 (0053 CH §2) — a person asks on the Q&A tab and an AI answers, so two more
// existing v0.1 endpoints are wrapped (still no new server API):
//   ask      POST /api/v1/q/{doc_id}/questions { asker_kind: 'human', questions: [{ body }] }
//            -> { ok, doc_id, added_item_ids: [id] }
//   ai       POST /api/v1/q/{doc_id}/items/{item_id}/answers/ai-request { provider_id? } -> { ok, doc_id,
//            item_id, run_id, status, provider }; the AI run posts its answer on the same question
//            (author_kind 'ai'), which arrives as `qna_answer_registered`. Its admission refusals keep
//            the ai-invoke envelope: a flat `code` (409 run_in_progress, no_enabled_provider …).
//            flowgate.v02.0056 F1 (0056 CH §3): the caller names the AI (`provider_id`, the route's
//            AiAnswerRequest body); without one the project's provider chain picks.
//   `answers/ai-mention` (copy a mention for one's own worker) stays unwrapped.
//
// flowgate.v02test.0051 C — what the server records about who answers and whether the past answers were searched:
//   item.responder       { state: 'dispatched' | 'failed' | 'user_decision' | 'answered', run_id, error_code,
//                          error_message, attempts, updated_at } | null  (null: no automatic responder touched it)
//   item.search_summary  { searched, status, found_count, returned_count, reused_answer, reuse_rejection_reason, ... }
//                          (`status: 'no_record'` for a legacy question)
//   ask 200 `status: 'reused_answer'` (no question added, the past answer comes back) and 409
//   `existing_answer_requires_reason` (do not resend as it is: use the existing answer or give a reason).
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
  /** The person author's name (`author_name`); null for an AI or a person not found. */
  readonly authorName: string | null
  /** Ids of the picked options (at most one today). */
  readonly selectedOptionIds: readonly string[]
  readonly provider: QaProvider | null
  readonly createdAt: string | null
}

/**
 * The automatic responder's record of a question (`item.responder`):
 * dispatched = an AI is answering, failed = a technical failure (error_code / error_message), user_decision = only a
 * person can decide it, answered = the responder's answer was stored.
 */
export interface QaResponder {
  readonly state: string
  readonly runId: string | null
  readonly errorCode: string | null
  readonly errorMessage: string | null
  readonly attempts: number
  readonly updatedAt: string | null
}

/** Where a reused past answer came from: the document, the Q number and who answered it. */
export interface QaReusedAnswer {
  readonly answerId: number | null
  readonly docId: string | null
  readonly seq: number | null
  /** The answerer as the server labelled it (`author_label`); null when it did not say. */
  readonly answerer: string | null
  /** The past answer's text (`answer_body`), so the reuse can be checked against its source; null when not sent. */
  readonly answerBody: string | null
}

/**
 * What the server recorded about the search of past answers before this question was registered
 * (`item.search_summary`). `recorded` is false for a legacy question (no record): the screen says so.
 */
export interface QaSearchSummary {
  readonly recorded: boolean
  /** The search ran (true), did not (false), or is not known (null). */
  readonly searched: boolean | null
  /** The server's status (`registered`, `search_failed`, `no_record`, ...). */
  readonly status: string | null
  /** How many past answers matched; null when not known (a failed search too). */
  readonly foundCount: number | null
  readonly reusedAnswer: QaReusedAnswer | null
  /** Why the offered past answer was not used, when the asker gave one. */
  readonly reuseRejectionReason: string | null
}

/**
 * One search-before-register record of the document (`search_prechecks[]` of `GET /q/{doc_id}`, and the body of the
 * 200 `reused_answer` / 409 answers). A record whose `addedItemIds` is empty registered no question: the reuse of
 * an existing answer, a refused resend, a failed search. It is the only trace of those searches, so the screen keeps it.
 */
export interface QaSearchPrecheck {
  readonly precheckId: string | null
  /** The server's status: `reused_answer`, `existing_answer_requires_reason`, `search_failed`, `registered`, ... */
  readonly status: string | null
  readonly searched: boolean | null
  /** How many past answers matched; null when not known (a failed search too). */
  readonly foundCount: number | null
  readonly reusedAnswer: QaReusedAnswer | null
  readonly reuseRejectionReason: string | null
  /** The questions this search registered; empty: no question was registered. */
  readonly addedItemIds: readonly number[]
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
  /** The automatic responder's record; null when none touched the question (or an older server). */
  readonly responder: QaResponder | null
  /** The search record; `recorded: false` when the server has none (legacy / older server). */
  readonly searchSummary: QaSearchSummary
}

/** The questions of one document. */
export interface QaThread {
  readonly docId: string
  /** The container status ('pending' / 'done'), null without a container. */
  readonly status: string | null
  readonly items: readonly QaItem[]
  /** The search records of the document (`search_prechecks`), questions registered or not; empty from an older server. */
  readonly searchPrechecks: readonly QaSearchPrecheck[]
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
    authorName: text(row.author_name),
    selectedOptionIds: list(row.selected_options).filter((id): id is string => typeof id === 'string'),
    provider: toQaProvider(row.author_provider),
    createdAt: text(row.created_at),
  }
}

export function toQaResponder(raw: unknown): QaResponder | null {
  const row = record(raw)
  const state = text(row.state)
  if (state === null) return null
  return {
    state,
    runId: text(row.run_id),
    errorCode: text(row.error_code),
    errorMessage: text(row.error_message),
    attempts: integer(row.attempts) ?? 0,
    updatedAt: text(row.updated_at),
  }
}

function toQaReusedAnswer(raw: unknown): QaReusedAnswer | null {
  const row = record(raw)
  const answerId = integer(row.answer_id)
  const docId = text(row.doc_id)
  if (answerId === null && docId === null) return null
  const provider = record(row.author_provider)
  return {
    answerId,
    docId,
    seq: integer(row.seq),
    answerer: text(row.author_label) ?? text(provider.ai_provider_name) ?? text(row.author_name),
    answerBody: text(row.answer_body),
  }
}

/** One `search_prechecks[]` record (or the body of an add-questions answer) -> QaSearchPrecheck. */
export function toQaSearchPrecheck(raw: unknown): QaSearchPrecheck {
  const row = record(raw)
  return {
    precheckId: text(row.precheck_id),
    status: text(row.status),
    searched: typeof row.searched === 'boolean' ? row.searched : null,
    foundCount: integer(row.found_count),
    reusedAnswer: toQaReusedAnswer(row.reused_answer),
    reuseRejectionReason: text(row.reuse_rejection_reason),
    addedItemIds: list(row.added_item_ids).flatMap((id) => integer(id) ?? []),
  }
}

const NO_SEARCH_RECORD: QaSearchSummary = { recorded: false, searched: null, status: null, foundCount: null, reusedAnswer: null, reuseRejectionReason: null }

/** `item.search_summary` -> QaSearchSummary; an absent block or `status: 'no_record'` is "no record" (never "searched"). */
export function toQaSearchSummary(raw: unknown): QaSearchSummary {
  const row = record(raw)
  const status = text(row.status)
  if (Object.keys(row).length === 0 || status === 'no_record') return NO_SEARCH_RECORD
  return {
    recorded: true,
    searched: typeof row.searched === 'boolean' ? row.searched : null,
    status,
    foundCount: integer(row.found_count),
    reusedAnswer: toQaReusedAnswer(row.reused_answer),
    reuseRejectionReason: text(row.reuse_rejection_reason),
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
    responder: toQaResponder(row.responder),
    searchSummary: toQaSearchSummary(row.search_summary),
  }
}

/** The `GET /q/{doc_id}` response -> QaThread. Throws when it carries no Q&A of a document. */
export function toQaThread(raw: unknown): QaThread {
  const qa = record(record(raw).qa)
  const docId = text(qa.doc_id) ?? text(qa.q_id)
  if (docId === null) throw new Error('Q&A response without doc_id')
  return {
    docId,
    status: text(qa.status),
    items: list(qa.items).flatMap((item) => toQaItem(item) ?? []),
    searchPrechecks: list(qa.search_prechecks).map(toQaSearchPrecheck),
  }
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
  /**
   * The server found the same answer already (200 `status: 'reused_answer'`, no question added): the past answer
   * and where it came from. Null when the question was registered.
   */
  readonly reusedAnswer: QaReusedAnswer | null
  /**
   * The search record of a reuse (id, found count, source): set with `reusedAnswer`, null when the question was
   * registered. The same record comes back in `search_prechecks` of the next read, which is what survives a reload.
   */
  readonly precheck: QaSearchPrecheck | null
}

/** The add-questions response -> QaAskResult. */
export function toQaAskResult(raw: unknown): QaAskResult {
  const row = record(raw)
  const reused = text(row.status) === 'reused_answer' ? toQaReusedAnswer(row.reused_answer) : null
  return {
    docId: text(row.doc_id),
    itemIds: list(row.added_item_ids).flatMap((id) => integer(id) ?? []),
    reusedAnswer: reused,
    precheck: reused ? toQaSearchPrecheck(row) : null,
  }
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

/**
 * Asks an AI to answer one question (the answer lands on the question): the named provider, else the
 * project's provider chain.
 */
export async function qaRequestAiAnswer(docId: string, itemId: number, providerId: string | null = null): Promise<QaAiRequestResult> {
  const response = await postRequest<unknown>(
    `${API}/q/${encodeURIComponent(docId)}/items/${encodeURIComponent(String(itemId))}/answers/ai-request`,
    providerId ? { provider_id: providerId } : {},
  )
  return toQaAiRequestResult(response.data)
}

/**
 * Why a Q&A call was refused:
 * running = an AI run owns the group (423 GROUP_AI_RUN_LOCKED, or the ai-request admission's 409
 * run_in_progress / run_still_live); no_provider = no AI provider can take the ai-request
 * (no_enabled_provider, no_provider_registered, provider_unavailable / disabled); disposed = the
 * group was disposed (a 409 without a code); reuse_required = a 409 `existing_answer_requires_reason` (a past
 * answer fits: use it, or resend with a reason — never the same request again); forbidden 403; not_found
 * 404 (the document or the question is gone); invalid 400 / 422; network = no answer.
 */
export type QaErrorKind = 'running' | 'no_provider' | 'disposed' | 'reuse_required' | 'forbidden' | 'not_found' | 'invalid' | 'network' | 'other'

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
  else if (code === 'existing_answer_requires_reason') kind = 'reuse_required'
  else if (status === 409) kind = code === null || code === 'group_disposed' ? 'disposed' : 'other'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}

/** Complete project open-question snapshot; never use the truncated notification feed. */
export interface QaOpenItem { doc_id: string; seq: number; title: string; type_code: string }
export async function qaOpenItems(projectId: string): Promise<QaOpenItem[]> {
  const response = await getRequest<{ items: QaOpenItem[] }>(`${API}/q`, { project_id: projectId })
  if (!Array.isArray(response.data.items)) throw new Error('Invalid open-question snapshot')
  return response.data.items
}
