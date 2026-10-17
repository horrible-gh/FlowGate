// flowgate.v02.0048 T#3 — skeleton of the kindSwitch document API (D#1 §6.3). Owned by T#10 (kindSwitch), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `kindSwitch`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   switch   PATCH /api/v1/documents/{doc_id}/root-type  { new_type: 'R' | 'B' }
//
// flowgate.v02.0048 T#10 — the R↔B kind switch on the existing v0.1 endpoint above (no new API):
//   the answer is `{ data: <the document row> }`: the new identity (`…-R` ↔ `…-B`, a new doc_id,
//   file name and inbound references), or the same row when it already had the asked type.
// The server sends no event for it (D#2 §3.1); the caller reads the Canvas again itself.
//
// Refusals keep the server's shape and are thrown as they are; kindSwitchErrorOf reads them
// (D#2 §3.3 / §3.4). Two families answer:
//   - the route's guards and the converter: HTTPException with a `detail` string — 409 group
//     disposed, 409 the workflow decision was taken (a sequence exists: every v0.1 Flow), 409 child
//     documents reference the root, 409 the target doc_id exists, 422 not a root / bad type, 404;
//     the mutation policy envelope `error.code` (423 GROUP_AI_RUN_LOCKED while an AI run owns the group)
//   - the v0.1 contact of the engine router (D#2 §4.2): the flat `{ error, doc_id, group_id,
//     graph_revision_no }` 409 — `flow_owned_by_graph` (Graph Flow), `flow_ownership_conflict`
// Only the status, the code and the leading words of a detail are read, never shown.
//
// flowgate.v02.0064 T#4 (P0006 §3.1 / §3.3 / §3.5 / §6.1): a root with child documents (or a member of a
// succession chain) is no longer refused — it is succeeded by a new document of the other kind. The
// document read with `include=root_conversion` (kindSwitchPreviewRead) carries the server's judgement
// (number swap / succession / refused, the new or expected number, the child count) and the
// `converted_from` link (also read alone, without the judgement: kindSwitchOriginRead); the switch answer adds
// `conversion`. The new refusals answer a flat
// `{ error: '<code>', detail, doc_id, … }` 409 and are classified by that code.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, patchRequest } from '@shared/api'
import { toDocumentRecord, type DocumentRecord } from './document'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

/** The two kinds a workflow root switches between: requirement and bug. */
export type KindSwitchType = 'R' | 'B'

/** How a switch runs: the number changes in place, or a new document succeeds the root. */
export type KindSwitchMode = 'number_swap' | 'succession'

/** What the switch answer says it did (`conversion`); absent on a same-kind answer and on an older server. */
export interface KindSwitchConversion {
  readonly mode: KindSwitchMode
  readonly oldDocId: string
  readonly newDocId: string
  readonly childCount: number
}

export interface KindSwitchResult {
  /** The document after the switch (its doc_id is the new identity). */
  readonly document: DocumentRecord
  /** The doc_id the switch was asked for. */
  readonly previousDocId: string
  /** The identity changed (false: the document already had the asked type). */
  readonly renamed: boolean
  /** The server's account of the switch, or null. */
  readonly conversion: KindSwitchConversion | null
}

const modeOf = (value: unknown): KindSwitchMode | null =>
  value === 'number_swap' || value === 'succession' ? value : null

const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0)

function toConversion(raw: unknown): KindSwitchConversion | null {
  const row = record(raw)
  const mode = modeOf(row.mode)
  const oldDocId = text(row.old_doc_id)
  const newDocId = text(row.new_doc_id)
  if (!mode || !oldDocId || !newDocId) return null
  return { mode, oldDocId, newDocId, childCount: count(row.child_count) }
}

/** The root-type response (`{ data: row, conversion? }`) -> KindSwitchResult. Throws without a document. */
export function toKindSwitchResult(raw: unknown, previousDocId: string): KindSwitchResult {
  const row = record(raw)
  const document = toDocumentRecord('data' in row ? row.data : row)
  return { document, previousDocId, renamed: document.docId !== previousDocId, conversion: toConversion(row.conversion) }
}

/** The server's judgement of a switch to the other kind (P0006 §3.1 `root_conversion`). */
export interface KindSwitchPreviewRefusal {
  /** The refusal code (`root_superseded`, `decision_taken`, …). */
  readonly code: string
  /** The read state / identity reason, when the code has one. */
  readonly reason: string | null
  readonly mode: KindSwitchMode | null
  readonly targetDocId: string | null
  readonly liveRootDocId: string | null
}

export interface KindSwitchPreviewJudgement {
  readonly targetType: KindSwitchType | null
  readonly decision: KindSwitchMode | 'refused'
  readonly mode: KindSwitchMode | null
  readonly refusal: KindSwitchPreviewRefusal | null
  /** The new document number; with `newDocExact` false it is the expected one (fixed when the switch runs). */
  readonly newDocId: string | null
  readonly newDocExact: boolean
  readonly childCount: number
  /** The root is a member of a succession chain. */
  readonly chained: boolean
  readonly liveRootDocId: string | null
}

/** Where a succeeded document came from (`converted_from`). */
export interface KindSwitchConvertedFrom {
  readonly docId: string
  readonly at: string | null
  readonly by: string | null
}

/** The document state the switch reads: the review facts plus the judgement and the succession links. */
export interface KindSwitchPreview {
  readonly record: DocumentRecord
  /** The document's group was disposed (a DC document exists). */
  readonly groupDisposed: boolean
  /** The judgement, or null (not a root, a server that does not judge, or a judgement that could not be read). */
  readonly conversion: KindSwitchPreviewJudgement | null
  readonly convertedFrom: KindSwitchConvertedFrom | null
  /** The live root of this R/B document (the chain's end when it was succeeded), or null. */
  readonly liveRootDocId: string | null
}

function toJudgement(raw: unknown): KindSwitchPreviewJudgement | null {
  const row = record(raw)
  const decision = row.decision === 'refused' ? 'refused' : modeOf(row.decision)
  if (!decision) return null
  const refusalRow = record(row.refusal)
  const refusalCode = text(refusalRow.code)
  const target = text(row.target_type)
  return {
    targetType: target === 'R' || target === 'B' ? target : null,
    decision,
    mode: modeOf(row.mode),
    refusal: refusalCode
      ? {
          code: refusalCode,
          reason: text(refusalRow.reason),
          mode: modeOf(refusalRow.mode),
          targetDocId: text(refusalRow.target_doc_id),
          liveRootDocId: text(refusalRow.live_root_doc_id),
        }
      : null,
    newDocId: text(row.new_doc_id),
    newDocExact: row.new_doc_exact === true,
    childCount: count(row.child_count),
    chained: row.chained === true,
    liveRootDocId: text(row.live_root_doc_id),
  }
}

function toConvertedFrom(raw: unknown): KindSwitchConvertedFrom | null {
  const row = record(raw)
  const docId = text(row.doc_id)
  return docId ? { docId, at: text(row.at), by: text(row.by) } : null
}

/** The document read with the judgement -> KindSwitchPreview. Throws when it carries no doc_id. */
export function toKindSwitchPreview(raw: unknown): KindSwitchPreview {
  const row = record(raw)
  return {
    record: toDocumentRecord(raw),
    groupDisposed: row.group_disposed === true,
    conversion: toJudgement(row.root_conversion),
    convertedFrom: toConvertedFrom(row.converted_from),
    liveRootDocId: text(row.live_root_doc_id),
  }
}

/** Reads the document state with the switch judgement (a pure read: no number is reserved). */
export async function kindSwitchPreviewRead(docId: string): Promise<KindSwitchPreview> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}`, { include: 'root_conversion' })
  return toKindSwitchPreview(response.data)
}

/**
 * Reads where a document came from by a succession (`converted_from`), or null: the plain document read, no
 * judgement (the Inspector's 「전환 전 문서」 link, on every ownership). Transport errors are thrown as they are.
 */
export async function kindSwitchOriginRead(docId: string): Promise<KindSwitchConvertedFrom | null> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}`)
  return toConvertedFrom(record(response.data).converted_from)
}

/** Switches a requirement / bug root to the other kind. Transport errors are thrown as they are. */
export async function kindSwitchConvert(docId: string, newType: KindSwitchType): Promise<KindSwitchResult> {
  const response = await patchRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/root-type`, { new_type: newType })
  return toKindSwitchResult(response.data, docId)
}

/**
 * Why a switch was refused:
 * superseded = the root was already succeeded by a later document (409 root_superseded);
 * body_unreadable = the root body cannot be read (409 root_body_unreadable);
 * identity_unsafe = the front matter identity fields cannot be rewritten (409 front_matter_identity_unsafe);
 * attachment = an attachment blocks it (409 attachment_target_rows_exist / attachment_path_unsafe /
 * attachments_changed / attachment_target_exists / attachment_copy_mismatch);
 * graph = the Flow is owned by a Graph definition (409 flow_owned_by_graph);
 * ownership_conflict = both engines claim the Flow (409 flow_ownership_conflict);
 * decided = the workflow decision was taken (409, a sequence exists); children = documents already
 * reference the root (409); collision = the target doc_id exists (409); disposed = the group was
 * disposed (409); running = an AI run owns the group (423 GROUP_AI_RUN_LOCKED, run_in_progress);
 * conflict = another 409; invalid = 400 / 422 (not a root, bad type); forbidden 403; not_found 404;
 * network = no answer.
 */
export type KindSwitchErrorKind =
  | 'superseded'
  | 'body_unreadable'
  | 'identity_unsafe'
  | 'attachment'
  | 'graph'
  | 'ownership_conflict'
  | 'decided'
  | 'children'
  | 'collision'
  | 'disposed'
  | 'running'
  | 'conflict'
  | 'invalid'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'other'

export interface KindSwitchError {
  readonly kind: KindSwitchErrorKind
  readonly status: number | null
  readonly code: string | null
}

const RUNNING_CODES = new Set(['GROUP_AI_RUN_LOCKED', 'run_in_progress', 'run_still_live'])

/** The flat `error` codes of the conversion refusals (P0006 §3.5) -> kind. */
const CODE_KINDS: ReadonlyMap<string, KindSwitchErrorKind> = new Map([
  ['root_superseded', 'superseded'],
  ['root_body_unreadable', 'body_unreadable'],
  ['front_matter_identity_unsafe', 'identity_unsafe'],
  ['attachment_target_rows_exist', 'attachment'],
  ['attachment_path_unsafe', 'attachment'],
  ['attachments_changed', 'attachment'],
  ['attachment_target_exists', 'attachment'],
  ['attachment_copy_mismatch', 'attachment'],
])

/** The judgement's refusal codes (P0006 §3.1 table) -> the kind its reason is shown with. */
const REFUSAL_KINDS: ReadonlyMap<string, KindSwitchErrorKind> = new Map([
  ...CODE_KINDS,
  ['invalid_target_type', 'invalid'],
  ['not_root', 'invalid'],
  ['not_found', 'not_found'],
  ['group_disposed', 'disposed'],
  ['group_ai_running', 'running'],
  ['flow_owned_by_graph', 'graph'],
  ['flow_ownership_conflict', 'ownership_conflict'],
  ['decision_taken', 'decided'],
  ['target_id_exists', 'collision'],
])

/** The kind a judged refusal is shown as (`other` for a code this client does not know). */
export function kindSwitchRefusalKind(code: string): KindSwitchErrorKind {
  return REFUSAL_KINDS.get(code) ?? 'other'
}

/** Leading words of the converter's / guards' 409 detail strings -> kind (the text is never shown). */
const DETAIL_KINDS: readonly (readonly [RegExp, KindSwitchErrorKind])[] = [
  [/^Modification not allowed: the group has been disposed/i, 'disposed'],
  [/^group_disposed/i, 'disposed'],
  [/^Cannot convert root type after the workflow decision/i, 'decided'],
  [/^Cannot convert root type: child documents/i, 'children'],
  [/^Target doc_id already exists/i, 'collision'],
]

/** What a failed switch threw, as kind / status / code. */
export function kindSwitchErrorOf(error: unknown): KindSwitchError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  // The contact answers a flat `{ error: '<code>' }`; the policy an `error` object; ai-invoke a flat `code`.
  const code = normalized.code ?? text(data.error)?.trim() ?? text(data.code)?.trim() ?? null
  const detail = typeof data.detail === 'string' ? data.detail.trim() : ''
  const status = normalized.status
  let kind: KindSwitchErrorKind
  if (normalized.network) kind = 'network'
  else if (code === 'flow_owned_by_graph') kind = 'graph'
  else if (code === 'flow_ownership_conflict') kind = 'ownership_conflict'
  else if (status === 423 || (code !== null && RUNNING_CODES.has(code))) kind = 'running'
  else if (code === 'group_disposed') kind = 'disposed'
  else if (code !== null && CODE_KINDS.has(code)) kind = CODE_KINDS.get(code) as KindSwitchErrorKind
  else if (status === 409) kind = DETAIL_KINDS.find(([pattern]) => pattern.test(detail))?.[1] ?? 'conflict'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}
