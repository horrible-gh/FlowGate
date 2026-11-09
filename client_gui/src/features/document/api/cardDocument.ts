// flowgate.v02.0127 T#5 — the card document transport of the Designer (0009-L §2.11~§2.15; P#1 /api/v2).
// A card's document is read and revised only through the card's own coordinates (flow / node / owner slot);
// there is no v1 fallback anywhere in the client (a v1 `card_document_edit_required` is shown, never retried).
//   read      GET  /api/v2/flows/{flow}/nodes/{node}/slots/{slot}/document
//   revise    POST …/document/revisions                { expected_doc_id, expected_revision, content, idempotency_key }
// TS structured editing (§2.15.1 / §2.15.3), same snapshot rules:
//   spec      GET  …/document/test-spec     POST …/document/test-spec/revisions   { …, cases, title?, intro?, locale? }
//   brief     GET  …/document/test-brief    POST …/document/test-brief/revisions  { …, text }
// A success is never judged by HTTP status alone: `cardDocumentConfirmRevision` checks ok / doc_id / N+1 / updated_at /
// line / replayed against the request's own pending target, and anything else is an uncertain result.
// flowgate.v02.0157 — a standalone document (no card line holds it) is edited by its doc_id only:
//   read      GET  /api/v2/documents/{doc_id}             -> the same snapshot + edit_path (card / non_card / unavailable)
//   revise    POST /api/v2/documents/{doc_id}/revisions   { expected_revision, content, idempotency_key }
// The server alone classifies the document; the client starts a standalone session only for edit_path=non_card, and
// a standalone answer carries no card line (`cardDocumentStandaloneConfirmRevision`). `DocumentEditTarget` is either one.
import { getRequest, postRequest } from '@shared/api'
import { normalizeApiError, normalizeApiInput } from '@shared/apiErrors'

const API = '/api/v2'

/** The card coordinates an editing session is bound to (§2.11). `projectId` only distinguishes clients; the server authorizes. */
export interface CardDocumentContext {
  readonly projectId: string
  readonly flowId: string
  readonly nodeKey: string
  readonly ownerSlot: string
  /** The document id the opener showed for the line. */
  readonly openedDocId: string
}

/** flowgate.v02.0157 — a standalone document's edit target: the document id alone (the server keeps the rest). */
export interface StandaloneDocumentTarget {
  readonly kind: 'document'
  readonly docId: string
}

/** What an edit session saves to: a card line's coordinates, or a standalone document's id. */
export type DocumentEditTarget = CardDocumentContext | StandaloneDocumentTarget

export function cardDocumentStandaloneTarget(docId: string): StandaloneDocumentTarget {
  return { kind: 'document', docId }
}

export function cardDocumentIsStandalone(target: DocumentEditTarget | null | undefined): target is StandaloneDocumentTarget {
  return !!target && 'kind' in target && target.kind === 'document'
}

/** The card coordinates of a target, or null for a standalone document (card-only callers: TS, structured saves). */
export function cardDocumentContextOf(target: DocumentEditTarget | null | undefined): CardDocumentContext | null {
  return target && !cardDocumentIsStandalone(target) ? target : null
}

/** One readable-or-not snapshot of the card's document (P#1 read). */
export interface CardDocumentSnapshot {
  readonly docId: string
  readonly revisionNo: number
  readonly updatedAt: string | null
  /** null unless `bodyState` is `readable`; an empty string is a readable empty body. */
  readonly content: string | null
  readonly bodyState: string
  readonly line: unknown
  /** TS structured projection of the same snapshot, when the structured read asked for it. */
  readonly projection?: unknown
  /** flowgate.v02.0157: the server's classification on a doc_id read (`card` / `non_card` / `unavailable`). */
  readonly editPath?: string
}

export interface CardRevisionConfirmed {
  readonly docId: string
  readonly revisionNo: number
  readonly updatedAt: string
  readonly replayed: boolean
  readonly line: unknown
}

type Raw = Record<string, unknown>

export function cardDocumentPath(ctx: Pick<CardDocumentContext, 'flowId' | 'nodeKey' | 'ownerSlot'>): string {
  return `${API}/flows/${encodeURIComponent(ctx.flowId)}/nodes/${encodeURIComponent(ctx.nodeKey)}/slots/${encodeURIComponent(ctx.ownerSlot)}/document`
}

const isRecord = (value: unknown): value is Raw => typeof value === 'object' && value !== null && !Array.isArray(value)
const isInt = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value)

/** The v2 read body -> snapshot. A malformed body throws (the caller keeps the session as it is). */
export function toCardDocumentSnapshot(raw: unknown): CardDocumentSnapshot {
  const row = isRecord(raw) ? raw : {}
  if (typeof row.doc_id !== 'string' || !row.doc_id || !isInt(row.revision_no) || typeof row.body_state !== 'string') {
    throw new Error('card document response is malformed')
  }
  const content = row.content === null || row.content === undefined ? null : row.content
  if (content !== null && typeof content !== 'string') throw new Error('card document content is malformed')
  return {
    docId: row.doc_id,
    revisionNo: row.revision_no,
    updatedAt: typeof row.updated_at === 'string' ? row.updated_at : null,
    // A non-readable body never carries text; an empty string stays a readable empty body.
    content: row.body_state === 'readable' ? content : null,
    bodyState: row.body_state,
    line: row.line ?? null,
    ...('projection' in row ? { projection: row.projection } : {}),
    ...(typeof row.edit_path === 'string' ? { editPath: row.edit_path } : {}),
  }
}

export async function cardDocumentRead(ctx: CardDocumentContext): Promise<CardDocumentSnapshot> {
  const response = await getRequest<unknown>(cardDocumentPath(ctx))
  return toCardDocumentSnapshot(response.data)
}

export type CardStructuredCommand =
  | { readonly kind: 'ts_spec'; readonly cases: readonly Record<string, unknown>[]; readonly title?: string; readonly intro?: string; readonly locale?: string }
  | { readonly kind: 'ts_brief'; readonly text: string }

/** A request the session froze: sent as it is, again only by an explicit retry. */
export interface CardRevisionRequest {
  readonly expectedDocId: string
  readonly expectedRevision: number
  readonly idempotencyKey: string
  /** Whole-body save. */
  readonly content?: string
  /** Structured TS command (cases or brief), never combined with `content`. */
  readonly command?: CardStructuredCommand
}

function revisionBody(request: CardRevisionRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    expected_doc_id: request.expectedDocId,
    expected_revision: request.expectedRevision,
    idempotency_key: request.idempotencyKey,
  }
  const command = request.command
  if (!command) return { ...body, content: request.content ?? '' }
  if (command.kind === 'ts_brief') return { ...body, text: command.text }
  const spec: Record<string, unknown> = { ...body, cases: command.cases }
  for (const name of ['title', 'intro', 'locale'] as const) {
    if (command[name] !== undefined) spec[name] = command[name]
  }
  return spec
}

export function cardDocumentRevisionPath(ctx: CardDocumentContext, command?: CardStructuredCommand): string {
  const base = cardDocumentPath(ctx)
  if (!command) return `${base}/revisions`
  return `${base}/${command.kind === 'ts_spec' ? 'test-spec' : 'test-brief'}/revisions`
}

/** Sends the request; the raw response body is returned unjudged (use `cardDocumentConfirmRevision`). */
export async function cardDocumentRevise(ctx: CardDocumentContext, request: CardRevisionRequest): Promise<unknown> {
  const response = await postRequest<unknown>(cardDocumentRevisionPath(ctx, request.command), revisionBody(request))
  return response.data
}

/** The structured read: the same snapshot plus the server's projection (null when the body is not readable). */
export async function cardDocumentStructuredRead(ctx: CardDocumentContext, kind: 'ts_spec' | 'ts_brief'): Promise<CardDocumentSnapshot> {
  const response = await getRequest<unknown>(`${cardDocumentPath(ctx)}/${kind === 'ts_spec' ? 'test-spec' : 'test-brief'}`)
  return toCardDocumentSnapshot(response.data)
}

/**
 * A validated success for exactly this request: ok, same doc_id, integer expected+1, updated_at, replayed
 * boolean and a line. Otherwise null — the result is uncertain, never a success.
 */
export function cardDocumentConfirmRevision(raw: unknown, expectedDocId: string, expectedRevision: number): CardRevisionConfirmed | null {
  if (!isRecord(raw) || raw.ok !== true) return null
  if (raw.doc_id !== expectedDocId || !isInt(raw.revision_no) || raw.revision_no !== expectedRevision + 1) return null
  if (typeof raw.updated_at !== 'string' || !raw.updated_at || typeof raw.replayed !== 'boolean') return null
  if (!('line' in raw) || raw.line === undefined) return null
  return { docId: raw.doc_id, revisionNo: raw.revision_no, updatedAt: raw.updated_at, replayed: raw.replayed, line: raw.line }
}

// --- Standalone documents (flowgate.v02.0157) --------------------------------------------------------

export function cardDocumentStandalonePath(docId: string): string {
  return `${API}/documents/${encodeURIComponent(docId)}`
}

export async function cardDocumentStandaloneRead(target: StandaloneDocumentTarget): Promise<CardDocumentSnapshot> {
  const response = await getRequest<unknown>(cardDocumentStandalonePath(target.docId))
  return toCardDocumentSnapshot(response.data)
}

/** Whole-body save of a standalone document; the raw body is returned unjudged (`cardDocumentStandaloneConfirmRevision`). */
export async function cardDocumentStandaloneRevise(target: StandaloneDocumentTarget, request: CardRevisionRequest): Promise<unknown> {
  // Structured TS commands are card-only; a standalone target never sends one.
  if (request.command) throw new Error('a standalone document takes whole-body saves only')
  const response = await postRequest<unknown>(`${cardDocumentStandalonePath(target.docId)}/revisions`, {
    expected_revision: request.expectedRevision,
    content: request.content ?? '',
    idempotency_key: request.idempotencyKey,
  })
  return response.data
}

/**
 * A validated standalone success for exactly this request: ok, same doc_id, integer expected+1, updated_at, replayed
 * boolean and edit_path=non_card (there is no card line). Otherwise null — uncertain, never a success.
 */
export function cardDocumentStandaloneConfirmRevision(raw: unknown, expectedDocId: string, expectedRevision: number): CardRevisionConfirmed | null {
  if (!isRecord(raw) || raw.ok !== true || raw.edit_path !== 'non_card') return null
  if (raw.doc_id !== expectedDocId || !isInt(raw.revision_no) || raw.revision_no !== expectedRevision + 1) return null
  if (typeof raw.updated_at !== 'string' || !raw.updated_at || typeof raw.replayed !== 'boolean') return null
  return { docId: raw.doc_id, revisionNo: raw.revision_no, updatedAt: raw.updated_at, replayed: raw.replayed, line: null }
}

/** The success check of the target's own route (a card answer needs its line, a standalone one edit_path=non_card). */
export function cardDocumentTargetConfirmRevision(target: DocumentEditTarget, raw: unknown, expectedDocId: string, expectedRevision: number): CardRevisionConfirmed | null {
  return cardDocumentIsStandalone(target)
    ? cardDocumentStandaloneConfirmRevision(raw, expectedDocId, expectedRevision)
    : cardDocumentConfirmRevision(raw, expectedDocId, expectedRevision)
}

// --- Errors (§2.13) ---------------------------------------------------------------------------------

/** What a failed card call said: status, the v2 `error.code`, its params and the v1 `detail` string, if any. */
export interface CardApiError {
  readonly status: number | null
  readonly code: string | null
  readonly params: Readonly<Record<string, unknown>>
  readonly legacyDetail: string | null
  readonly network: boolean
}

export function cardDocumentErrorOf(error: unknown): CardApiError {
  const normalized = normalizeApiError(error)
  const { data } = normalizeApiInput(error)
  const source = normalized.source
  const params = isRecord(source.params) ? source.params : {}
  return {
    status: normalized.status,
    code: normalized.code,
    params,
    legacyDetail: typeof data.detail === 'string' ? data.detail : null,
    network: normalized.network,
  }
}
