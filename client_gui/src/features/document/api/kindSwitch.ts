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
import { normalizeApiError } from '@shared/apiErrors'
import { patchRequest } from '@shared/api'
import { toDocumentRecord, type DocumentRecord } from './document'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

/** The two kinds a workflow root switches between: requirement and bug. */
export type KindSwitchType = 'R' | 'B'

export interface KindSwitchResult {
  /** The document after the switch (its doc_id is the new identity). */
  readonly document: DocumentRecord
  /** The doc_id the switch was asked for. */
  readonly previousDocId: string
  /** The identity changed (false: the document already had the asked type). */
  readonly renamed: boolean
}

/** The root-type response (`{ data: row }`) -> KindSwitchResult. Throws without a document. */
export function toKindSwitchResult(raw: unknown, previousDocId: string): KindSwitchResult {
  const row = record(raw)
  const document = toDocumentRecord('data' in row ? row.data : row)
  return { document, previousDocId, renamed: document.docId !== previousDocId }
}

/** Switches a requirement / bug root to the other kind. Transport errors are thrown as they are. */
export async function kindSwitchConvert(docId: string, newType: KindSwitchType): Promise<KindSwitchResult> {
  const response = await patchRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/root-type`, { new_type: newType })
  return toKindSwitchResult(response.data, docId)
}

/**
 * Why a switch was refused:
 * graph = the Flow is owned by a Graph definition (409 flow_owned_by_graph);
 * ownership_conflict = both engines claim the Flow (409 flow_ownership_conflict);
 * decided = the workflow decision was taken (409, a sequence exists); children = documents already
 * reference the root (409); collision = the target doc_id exists (409); disposed = the group was
 * disposed (409); running = an AI run owns the group (423 GROUP_AI_RUN_LOCKED, run_in_progress);
 * conflict = another 409; invalid = 400 / 422 (not a root, bad type); forbidden 403; not_found 404;
 * network = no answer.
 */
export type KindSwitchErrorKind =
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
  else if (status === 409) kind = DETAIL_KINDS.find(([pattern]) => pattern.test(detail))?.[1] ?? 'conflict'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}
