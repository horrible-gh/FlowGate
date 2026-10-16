// flowgate.v02.0056 F2-2 — [작성] of a card line without a document (O3 flowgate.v02.0056.0009-TR §4-2):
//
//   write  POST /api/v1/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document  { content: <markdown> | null }
//          -> 201 { flow_id, node_key, slot, doc_type, doc_id, bound_as_result, line }
//          The server makes the line's document the way the card made its own (front matter by the server,
//          `content` after it; null = an empty draft). When the node runs (open in the Flow's active run) and
//          the line's type is the node's result type, the document is the node's result: `bound_as_result`
//          = true, the card waits for its approval (O1) and a body is required. `line` is the line after it,
//          in the card line shape (§4-1, `has_body` and the history counts included).
//
// Refusals are the Graph envelope `{ ok: false, error: { code, message, params } }` and are thrown as they are
// (cardLineErrorOf reads them): 409 slot_has_document (params.doc_id) / node_token_unavailable /
// node_in_instruction_stage / node_ai_working / flow_disposed / card_documents_unavailable, 422 invalid_request
// (params.errors[0] { field, reason }: content not_a_text · too_long · not_for_this_slot · empty_body, slot
// not_a_slot_of_this_card · slot_shares_a_document · doc_type_not_registered), 404 node_not_found /
// flow_not_found, 403 flow_access_denied.
import { normalizeApiError } from '@shared/apiErrors'
import { postRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

/** The longest content the server takes (O3 §4-2 `too_long`, max 500000). */
export const cardLineContentMaxLength = 500000

/** One card line as the write answers it (the Canvas card line shape, O3 §4-1). */
export interface CardLineRecord {
  readonly slot: string
  readonly docType: string | null
  readonly author: string | null
  readonly docId: string | null
  readonly title: string | null
  readonly status: string | null
  readonly docReviewStatus: string | null
  readonly hasBody?: boolean
  readonly bodyState?: string | null
  readonly reviewCount?: number
  readonly rejectionCount?: number
  readonly historyCount?: number
}

/** The 201 answer of a line write. */
export interface CardLineWriteResult {
  readonly flowId: string
  readonly nodeKey: string
  readonly slot: string
  readonly docType: string | null
  /** The new document. */
  readonly docId: string | null
  /** The document is the running node's result: the card now waits for its approval. */
  readonly boundAsResult: boolean
  /** The line after the write; null when the answer carried none. */
  readonly line: CardLineRecord | null
}

/** A card line of the answer -> CardLineRecord; null without a slot. Fields the server did not send stay absent. */
export function toCardLineRecord(raw: unknown): CardLineRecord | null {
  const line = record(raw)
  if (typeof line.slot !== 'string') return null
  const reviewCount = count(line.review_count)
  const rejectionCount = count(line.rejection_count)
  const historyCount = count(line.history_count)
  return {
    slot: line.slot,
    docType: text(line.doc_type),
    author: text(line.author),
    docId: text(line.doc_id),
    title: text(line.title),
    status: text(line.status),
    docReviewStatus: text(line.doc_review_status),
    ...(typeof line.has_body === 'boolean' ? { hasBody: line.has_body } : {}),
    ...(typeof line.body_state === 'string' || line.body_state === null ? { bodyState: line.body_state } : {}),
    ...(reviewCount !== null ? { reviewCount } : {}),
    ...(rejectionCount !== null ? { rejectionCount } : {}),
    ...(historyCount !== null ? { historyCount } : {}),
  }
}

/** The 201 body -> CardLineWriteResult (the request's keys stand in for missing ones). */
export function toCardLineWriteResult(raw: unknown, request: { flowId: string; nodeKey: string; slot: string }): CardLineWriteResult {
  const body = record(raw)
  return {
    flowId: text(body.flow_id) ?? request.flowId,
    nodeKey: text(body.node_key) ?? request.nodeKey,
    slot: text(body.slot) ?? request.slot,
    docType: text(body.doc_type),
    docId: text(body.doc_id),
    boundAsResult: body.bound_as_result === true,
    line: toCardLineRecord(body.line),
  }
}

/**
 * Writes the document of a card line that has none: `content` the markdown body after the server's front matter,
 * null for an empty draft (a work plan line takes no content). A refusal is thrown as it is (see cardLineErrorOf).
 */
export async function cardLineWrite(flowId: string, nodeKey: string, slot: string, content: string | null): Promise<CardLineWriteResult> {
  const response = await postRequest<unknown>(
    `${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/slots/${encodeURIComponent(slot)}/document`,
    { content },
  )
  return toCardLineWriteResult(response.data, { flowId, nodeKey, slot })
}

/** A refused line write as the screen reads it. */
export interface CardLineError {
  readonly code: string | null
  readonly status: number | null
  /** No answer reached the screen. */
  readonly network: boolean
  /** The server's message as it sent it (x-locale), or ''. */
  readonly message: string
  /** 422: the field and the reason of the first error (`content` / `empty_body` …). */
  readonly field: string | null
  readonly reason: string | null
  /** 409 slot_has_document: the document the line already has. */
  readonly docId: string | null
}

/** What a failed cardLineWrite threw: code, status, the server's message and what its params name. */
export function cardLineErrorOf(error: unknown): CardLineError {
  const normalized = normalizeApiError(error)
  const source = normalized.source as Raw
  const params = record(source.params)
  const errors = Array.isArray(params.errors) ? params.errors : []
  const first = record(errors.find((entry) => entry && typeof entry === 'object'))
  return {
    code: normalized.code,
    status: normalized.status,
    network: normalized.network,
    message: typeof source.message === 'string' ? source.message.trim() : '',
    field: text(first.field),
    reason: text(first.reason),
    docId: text(params.doc_id),
  }
}


/** Only known unreadable states warn; older servers and documentless lines do not. */
export const UNREADABLE_BODY_STATES: ReadonlySet<string> = new Set([
  'file_path_missing', 'file_not_found', 'path_unresolved', 'read_error',
])

export function lineWarns(line: { readonly docId?: string | null; readonly bodyState?: string | null }): boolean {
  return line.docId != null && typeof line.bodyState === 'string' && UNREADABLE_BODY_STATES.has(line.bodyState)
}
