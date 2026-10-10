// flowgate.v02.0048 T#3 — the common error reading of the document API (D#1 §6.2, owned by T#3).
// A document call throws the transport error as it is; a caller turns it into the HTTP status,
// the server code and a kind it can show a reason for (409 contention, 403 permission, 404 gone,
// no response). Only code and status are read — never a server message text (the same rule as
// @shared/apiErrors, whose normalizer this reuses).
import { normalizeApiError } from '@shared/apiErrors'

export type DocumentErrorKind = 'conflict' | 'forbidden' | 'not_found' | 'network' | 'other'

export interface DocumentError {
  readonly kind: DocumentErrorKind
  readonly status: number | null
  /** The server's error code (`error.code` / `detail.code`), when it sent one. */
  readonly code: string | null
}

const KIND_BY_STATUS: Readonly<Record<number, DocumentErrorKind>> = { 409: 'conflict', 403: 'forbidden', 404: 'not_found' }

/** What a failed document call threw, as status / code / kind. */
export function documentErrorOf(error: unknown): DocumentError {
  const normalized = normalizeApiError(error)
  const kind = normalized.network ? 'network' : (normalized.status !== null && KIND_BY_STATUS[normalized.status]) || 'other'
  return { kind, status: normalized.status, code: normalized.code }
}
