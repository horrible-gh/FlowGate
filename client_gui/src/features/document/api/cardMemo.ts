// flowgate.v02.0056 F2-2 — v0.2 card memos (O3 flowgate.v02.0056.0009-TR §4-5). A memo belongs to a card (the
// Flow's node), not to a document: it is no v0.1 M document (memo.ts keeps the v0.1 one), makes no document and
// starts no AI. A memo with `ai_ref` on is handed to the card's AI runs (instruction · result · review · rework),
// to a single AI call on the card's documents and to the Q&A AI answers of them.
//
//   list    GET    /api/v1/flows/{flow_id}/nodes/{node_key}/memos              -> 200 { flow_id, node_key, memos: [memo…] } (written order)
//   create  POST   /api/v1/flows/{flow_id}/nodes/{node_key}/memos              { title, body?, ai_ref? } -> 201 { flow_id, node_key, memo }
//   change  PATCH  /api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}    { title?, body?, ai_ref? } (one or more) -> 200 { …, memo }
//   delete  DELETE /api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}    -> 200 { flow_id, node_key, memo_id, deleted: true }
//
//   memo = { memo_id, title, body, ai_ref, created_by, created_at, updated_at, updated_by } (ISO 8601 UTC times)
//
// No server event announces a memo change: the screen follows its own answers and reads the list again.
// Refusals are the Graph envelope `{ ok: false, error: { code, message, params } }`, thrown as they are
// (cardMemoErrorOf): 422 invalid_request (params.errors[0] { field: title · body · ai_ref · <key>, reason:
// required · not_a_text · too_long · invalid_text · not_an_object · no_change · not_a_bool · unknown_field }),
// 404 memo_not_found / node_not_found / flow_not_found, 409 flow_disposed / card_memos_unavailable, 403
// flow_access_denied.
import { normalizeApiError } from '@shared/apiErrors'
import { deleteRequest, getRequest, patchRequest, postRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

/** The server's limits (O3 §4-5): a title of 1..200 characters on one line, a body of at most 20000. */
export const cardMemoTitleMaxLength = 200
export const cardMemoBodyMaxLength = 20000

export interface CardMemo {
  readonly memoId: string
  readonly title: string
  readonly body: string
  /** The memo is handed to the card's AI runs. */
  readonly aiRef: boolean
  readonly createdBy: string | null
  readonly createdAt: string | null
  readonly updatedAt: string | null
  readonly updatedBy: string | null
}

/** One memo of an answer -> CardMemo; null without a memo id. */
export function toCardMemo(raw: unknown): CardMemo | null {
  const row = record(raw)
  const memoId = typeof row.memo_id === 'number' && Number.isFinite(row.memo_id) ? String(row.memo_id) : text(row.memo_id)
  if (memoId === null) return null
  return {
    memoId,
    title: typeof row.title === 'string' ? row.title : '',
    body: typeof row.body === 'string' ? row.body : '',
    aiRef: row.ai_ref === true,
    createdBy: text(row.created_by),
    createdAt: text(row.created_at),
    updatedAt: text(row.updated_at),
    updatedBy: text(row.updated_by),
  }
}

/** The list answer -> the memos in the server's (written) order. */
export function toCardMemos(raw: unknown): CardMemo[] {
  const memos = record(raw).memos
  return Array.isArray(memos) ? memos.flatMap((item) => toCardMemo(item) ?? []) : []
}

const memosPath = (flowId: string, nodeKey: string) => `${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/memos`
const memoPath = (flowId: string, nodeKey: string, memoId: string) => `${memosPath(flowId, nodeKey)}/${encodeURIComponent(memoId)}`

/** Reads a card's memos (written order). */
export async function cardMemoList(flowId: string, nodeKey: string): Promise<CardMemo[]> {
  const response = await getRequest<unknown>(memosPath(flowId, nodeKey))
  return toCardMemos(response.data)
}

/** What a memo is written with: the title (required on create), the body and whether the AI refers to it. */
export interface CardMemoInput {
  readonly title?: string
  readonly body?: string
  readonly aiRef?: boolean
}

/** The request body of a create or a change: only the fields given, in the server's names. */
export function cardMemoPayload(input: CardMemoInput): Raw {
  return {
    ...(input.title !== undefined ? { title: input.title } : {}),
    ...(input.body !== undefined ? { body: input.body } : {}),
    ...(input.aiRef !== undefined ? { ai_ref: input.aiRef } : {}),
  }
}

function memoOf(raw: unknown): CardMemo {
  const memo = toCardMemo(record(raw).memo)
  if (!memo) throw new Error('card memo answer without a memo')
  return memo
}

/** Writes a new memo on the card; answers it. */
export async function cardMemoCreate(flowId: string, nodeKey: string, input: CardMemoInput): Promise<CardMemo> {
  const response = await postRequest<unknown>(memosPath(flowId, nodeKey), cardMemoPayload(input))
  return memoOf(response.data)
}

/** Changes a memo (the fields given only); answers it after the change. */
export async function cardMemoUpdate(flowId: string, nodeKey: string, memoId: string, input: CardMemoInput): Promise<CardMemo> {
  const response = await patchRequest<unknown>(memoPath(flowId, nodeKey, memoId), cardMemoPayload(input))
  return memoOf(response.data)
}

/** Deletes a memo. */
export async function cardMemoDelete(flowId: string, nodeKey: string, memoId: string): Promise<void> {
  await deleteRequest<unknown>(memoPath(flowId, nodeKey, memoId))
}

/** A refused memo call as the screen reads it. */
export interface CardMemoError {
  readonly code: string | null
  readonly status: number | null
  /** No answer reached the screen. */
  readonly network: boolean
  /** The server's message as it sent it (x-locale), or ''. */
  readonly message: string
  /** 422: the field and the reason of the first error. */
  readonly field: string | null
  readonly reason: string | null
}

/** What a failed memo call threw: code, status, the server's message and the first 422 error. */
export function cardMemoErrorOf(error: unknown): CardMemoError {
  const normalized = normalizeApiError(error)
  const source = normalized.source as Raw
  const errors = Array.isArray(record(source.params).errors) ? (record(source.params).errors as unknown[]) : []
  const first = record(errors.find((entry) => entry && typeof entry === 'object'))
  return {
    code: normalized.code,
    status: normalized.status,
    network: normalized.network,
    message: typeof source.message === 'string' ? source.message.trim() : '',
    field: text(first.field),
    reason: text(first.reason),
  }
}
