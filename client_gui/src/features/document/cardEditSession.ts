// flowgate.v02.0127 T#5 — the client editing session of a card document (0009-L §2.12 / §2.13 / §3.1).
// Pure state, no Vue and no HTTP: the window / upload / TS sessions hold one `EditSession` and move it only
// through the functions below, so the §3.1 transitions are one tested place.
//
// Fixed rules (parameters of §1):
//   single_inflight = 1                 one unresolved save request per session (`beginSave` answers null while SAVING)
//   automatic_rebase = 0                a read, an event or a conflict answer never replaces `base`; the only base
//                                       advance is the ACK of this session's own pending SAVED / REPLAY
//   automatic_retry_with_new_key = 0    an UNCERTAIN or 401-REJECTED request is resent as it is (same key, same bytes);
//                                       a new key exists only for an explicit new intent
//   401 unauthorized / 403 token_kind_not_allowed are REJECTED (rejected before commit), never UNCERTAIN.
// flowgate.v02.0157: a session's target is a card line (`CardDocumentContext`) or a standalone document
// (`StandaloneDocumentTarget`, doc_id only). A standalone base is adopted only when the server's read says
// edit_path=non_card; a card / unavailable answer keeps the editor BLOCKED (binding_required) as before.
import {
  cardDocumentTargetConfirmRevision,
  cardDocumentIsStandalone,
  type CardApiError,
  type CardDocumentSnapshot,
  type DocumentEditTarget,
  type CardRevisionConfirmed,
  type CardRevisionRequest,
  type CardStructuredCommand,
} from './api/cardDocument'

export type EditState =
  | 'VIEW'
  | 'LOADING_BASE'
  | 'CLEAN'
  | 'DIRTY'
  | 'SAVING'
  | 'REJECTED'
  | 'CONFLICT'
  | 'DETACHED'
  | 'UNCERTAIN'
  | 'BLOCKED'

export type PendingStatus = 'INFLIGHT' | 'UNCERTAIN' | 'REJECTED' | 'ACKNOWLEDGED'

/** The base a save is made against: the document, its revision and the text it was split from. */
export interface EditBase {
  readonly docId: string
  readonly revisionNo: number
  readonly updatedAt: string | null
  readonly frontmatter: string
  /** null after a structured ACK: the full N+1 text is not known until a snapshot of it is read. */
  readonly body: string | null
}

/** The frozen request (coordinates, target, submitted bytes, key). Nothing but the session's own answer changes `status`. */
export interface PendingRequest {
  readonly context: DocumentEditTarget
  readonly expectedDocId: string
  readonly expectedRevision: number
  readonly frontmatter: string
  readonly body: string
  /** The submitted whole text (frontmatter kept + body); '' for a structured command. */
  readonly text: string
  readonly key: string
  readonly command: CardStructuredCommand | null
  readonly status: PendingStatus
  /** The same pending may be sent again by an explicit retry (UNCERTAIN, or a 401 once re-authenticated). */
  readonly resendable: boolean
}

/** A rejected/uncertain answer kept for display. */
export interface SaveFailure {
  readonly code: string | null
  readonly status: number | null
  readonly params: Readonly<Record<string, unknown>>
  /** i18n key suffix under gui.document_window.error.* (§2.13). */
  readonly errorKey: string
}

export interface EditSession {
  readonly state: EditState
  readonly context: DocumentEditTarget | null
  /** The document id the editor was started for (the shown document); a read of another id is DETACHED. */
  readonly targetDocId: string | null
  readonly base: EditBase | null
  readonly draft: string
  readonly pending: PendingRequest | null
  /** Latest GET, kept apart from base/draft/pending. */
  readonly latest: CardDocumentSnapshot | null
  readonly failure: SaveFailure | null
  readonly blockReason: string | null
  readonly readSequence: number
  /** An event or poll saw the document change while editing; shown, never applied. */
  readonly remoteChanged: boolean
  /** 403 token_kind_not_allowed: no resend until the call setting is corrected. */
  readonly configBlocked: boolean
  /** idempotency_key_reused: the original request binding must be confirmed and a new intent started explicitly. */
  readonly needsNewIntent: boolean
}

export function newSession(): EditSession {
  return {
    state: 'VIEW', context: null, targetDocId: null, base: null, draft: '', pending: null, latest: null,
    failure: null, blockReason: null, readSequence: 0, remoteChanged: false, configBlocked: false, needsNewIntent: false,
  }
}

const with_ = (session: EditSession, patch: Partial<EditSession>): EditSession => ({ ...session, ...patch })

// --- Text helpers -----------------------------------------------------------------------------------

export function splitFrontmatter(content: string): { frontmatter: string; body: string } {
  if (!content.startsWith('---\n')) return { frontmatter: '', body: content }
  const closing = content.indexOf('\n---', 3)
  if (closing === -1) return { frontmatter: '', body: content }
  const end = closing + 4
  const rest = content.slice(end)
  return { frontmatter: content.slice(0, end), body: rest.startsWith('\n') ? rest.slice(1) : rest }
}

export function joinFrontmatter(frontmatter: string, body: string): string {
  return frontmatter ? `${frontmatter}\n${body}` : body
}

/** A UUID v4 idempotency key (§1 idempotency_key). */
export function newIdempotencyKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(bytes)
  else for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'))
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`
}

// --- Failure classification (§2.12 / §2.13) ---------------------------------------------------------

/** codes the server rejects before anything is stored (everything below is REJECTED unless noted). */
const KNOWN_REJECTIONS: ReadonlySet<string> = new Set([
  'unauthorized', 'token_kind_not_allowed', 'revision_conflict', 'slot_document_changed', 'no_document',
  'idempotency_key_reused', 'GROUP_AI_RUN_LOCKED', 'permission_denied', 'card_binding_missing',
  'expected_revision_missing', 'not_editable', 'invalid_content', 'invalid_request', 'workflow_revert_pending',
  'flow_disposed', 'node_not_found', 'flow_not_found',
])

export type FailureKind = 'rejected' | 'uncertain'

export interface ClassifiedFailure extends SaveFailure {
  readonly kind: FailureKind
}

/** The translation key suffix of an answer (§2.13 table); unknown / network / 5xx answers are a general or uncertain failure. */
export function saveFailureKey(error: CardApiError): string {
  const code = error.code
  if (error.legacyDetail === 'card_document_edit_required') return 'card_document_edit_required'
  if (error.status === 401) return 'unauthorized'
  if (code === 'not_editable') {
    const reason = typeof error.params.reason === 'string' ? error.params.reason : ''
    return reason ? `not_editable_${reason}` : 'not_editable'
  }
  if (code && KNOWN_REJECTIONS.has(code)) return code
  if (error.legacyDetail === 'file_exists_unrecorded') return 'file_exists_unrecorded'
  return error.network ? 'network' : 'save'
}

export function classifyFailure(error: CardApiError): ClassifiedFailure {
  const key = saveFailureKey(error)
  const status = error.status
  // Rejected before commit: the two auth answers, the v1 pointers and every registered v2 code. A network failure,
  // a 5xx or an unregistered answer is an uncertain result (the request may have been stored).
  const rejected =
    !error.network &&
    (status === 401 ||
      error.legacyDetail === 'card_document_edit_required' ||
      error.legacyDetail === 'file_exists_unrecorded' ||
      (error.code !== null && KNOWN_REJECTIONS.has(error.code)))
  return {
    kind: rejected ? 'rejected' : 'uncertain',
    code: error.code ?? (status === 401 ? 'unauthorized' : null),
    status,
    params: error.params,
    errorKey: key,
  }
}

// --- Transitions ------------------------------------------------------------------------------------

/** VIEW -> LOADING_BASE (card or standalone target present) or BLOCKED (no target: nothing to edit through). */
export function beginLoad(session: EditSession, context: DocumentEditTarget | null, targetDocId: string): { session: EditSession; seq: number } {
  const seq = session.readSequence + 1
  if (!context) {
    return { seq, session: with_(newSession(), { state: 'BLOCKED', blockReason: 'binding_required', readSequence: seq, targetDocId }) }
  }
  return {
    seq,
    session: with_(newSession(), { state: 'LOADING_BASE', context, targetDocId, readSequence: seq }),
  }
}

/**
 * A read answered. Stale sequences are dropped. LOADING_BASE adopts snapshot text + revision + doc_id together
 * (CLEAN) or blocks (unreadable / connection changed). In any other state only `latest` is filled.
 */
export function baseRead(session: EditSession, seq: number, snapshot: CardDocumentSnapshot): EditSession {
  if (seq !== session.readSequence) return session
  if (session.state !== 'LOADING_BASE') return latestRead(session, snapshot)
  if (cardDocumentIsStandalone(session.context) && snapshot.editPath !== 'non_card') {
    // The server says a card holds the document (or it cannot tell): only the card's coordinates may edit it.
    return with_(session, { state: 'BLOCKED', blockReason: 'binding_required', latest: snapshot })
  }
  if (snapshot.bodyState !== 'readable' || typeof snapshot.content !== 'string') {
    return with_(session, { state: 'BLOCKED', blockReason: snapshot.bodyState || 'body_unreadable', latest: snapshot })
  }
  if (session.targetDocId !== null && snapshot.docId !== session.targetDocId) {
    // The card's line points at another document now: never adopt it as the save base.
    return with_(session, { state: 'DETACHED', latest: snapshot, blockReason: 'slot_document_changed' })
  }
  const parts = splitFrontmatter(snapshot.content)
  return with_(session, {
    state: 'CLEAN', latest: snapshot, draft: parts.body,
    base: { docId: snapshot.docId, revisionNo: snapshot.revisionNo, updatedAt: snapshot.updatedAt, frontmatter: parts.frontmatter, body: parts.body },
  })
}

/** A failed base read: only a LOADING_BASE session is affected (BLOCKED with the reason). */
export function baseReadFailed(session: EditSession, seq: number, reason: string): EditSession {
  if (seq !== session.readSequence || session.state !== 'LOADING_BASE') return session
  return with_(session, { state: 'BLOCKED', blockReason: reason })
}

/** A later read (poll, event, conflict follow-up, upload reflect): latest only; base / draft / pending never change. */
export function latestRead(session: EditSession, snapshot: CardDocumentSnapshot): EditSession {
  const changed = !!session.base && (snapshot.docId !== session.base.docId || snapshot.revisionNo !== session.base.revisionNo)
  return with_(session, { latest: snapshot, remoteChanged: session.remoteChanged || changed })
}

/** A server event / local content-changed event: marks remote_changed, nothing else (§3.1 last-but-one rows). */
export function externalEvent(session: EditSession): EditSession {
  return session.state === 'VIEW' ? session : with_(session, { remoteChanged: true })
}

/** Input: CLEAN <-> DIRTY against the base; every other state keeps its state and only takes the text. */
export function typeDraft(session: EditSession, draft: string): EditSession {
  if (draft === session.draft) return session
  const next = with_(session, { draft })
  if (session.state === 'CLEAN' && session.base && draft !== session.base.body) return with_(next, { state: 'DIRTY' })
  if (session.state === 'DIRTY' && session.base && draft === session.base.body && !session.pending) return with_(next, { state: 'CLEAN' })
  return next
}

export type SaveBlock =
  | 'not_editable'
  | 'saving'
  | 'conflict'
  | 'detached'
  | 'blocked'
  | 'config'
  | 'needs_new_intent'
  | 'base_snapshot_required'

/**
 * The explicit [저장]. Returns the frozen request to send, or null with the reason it may not be sent.
 * - an unresolved pending that may be resent (UNCERTAIN / 401-REJECTED) is returned as it is: same key, same bytes;
 * - otherwise a new pending is frozen from the base and the current draft with `freshKey`.
 */
export function beginSave(
  session: EditSession,
  freshKey: () => string,
  command: CardStructuredCommand | null = null,
): { session: EditSession; request: PendingRequest | null; blocked: SaveBlock | null } {
  const stop = (blocked: SaveBlock) => ({ session, request: null, blocked })
  if (!session.context || !session.base) return stop('not_editable')
  if (session.state === 'SAVING') return stop('saving')
  if (session.state === 'CONFLICT') return stop('conflict')
  if (session.state === 'DETACHED') return stop('detached')
  if (session.state === 'BLOCKED' || session.state === 'LOADING_BASE' || session.state === 'VIEW') return stop('blocked')
  if (session.configBlocked) return stop('config')
  if (session.needsNewIntent) return stop('needs_new_intent')
  const pending = session.pending
  if (pending && pending.resendable && (pending.status === 'UNCERTAIN' || pending.status === 'REJECTED')) {
    const resent: PendingRequest = { ...pending, status: 'INFLIGHT' }
    return { session: with_(session, { state: 'SAVING', pending: resent, failure: null }), request: resent, blocked: null }
  }
  if (pending && pending.status === 'UNCERTAIN') return stop('blocked') // unreachable guard: uncertain is always resendable
  if (command !== null && cardDocumentIsStandalone(session.context)) return stop('not_editable') // structured saves are card-only
  if (command === null && session.base.body === null) return stop('base_snapshot_required')
  const body = session.draft
  const request: PendingRequest = {
    context: session.context,
    expectedDocId: session.base.docId,
    expectedRevision: session.base.revisionNo,
    frontmatter: session.base.frontmatter,
    body,
    text: command === null ? joinFrontmatter(session.base.frontmatter, body) : '',
    key: freshKey(),
    command,
    status: 'INFLIGHT',
    resendable: false,
  }
  return { session: with_(session, { state: 'SAVING', pending: request, failure: null }), request, blocked: null }
}

/** The wire request of a frozen pending. */
export function requestOf(pending: PendingRequest): CardRevisionRequest {
  return {
    expectedDocId: pending.expectedDocId,
    expectedRevision: pending.expectedRevision,
    idempotencyKey: pending.key,
    ...(pending.command ? { command: pending.command } : { content: pending.text }),
  }
}

export interface SaveOutcome {
  readonly session: EditSession
  /** The answer was ignored (an old window / another pending / no pending). */
  readonly ignored: boolean
  /** SAVED / REPLAY confirmed for this pending. */
  readonly confirmed: CardRevisionConfirmed | null
  /** After a confirmed save the full text equals the submission: the window returns to the view. */
  readonly toView: boolean
}

const own = (session: EditSession, key: string, context: DocumentEditTarget | null): boolean =>
  !!session.pending && session.pending.key === key && session.pending.status === 'INFLIGHT' && session.context === context

/**
 * An answer to the request of `key`. Only this session's own INFLIGHT pending can be acknowledged. A shape that
 * does not match (ok / doc_id / expected+1 / updated_at / replayed / line — or, for a standalone target,
 * edit_path=non_card instead of a line) is UNCERTAIN, never a success.
 */
export function saveAnswered(session: EditSession, key: string, context: DocumentEditTarget | null, raw: unknown): SaveOutcome {
  if (!own(session, key, context) || !session.pending) return { session, ignored: true, confirmed: null, toView: false }
  const pending = session.pending
  const confirmed = cardDocumentTargetConfirmRevision(pending.context, raw, pending.expectedDocId, pending.expectedRevision)
  if (confirmed === null) {
    const failure: SaveFailure = { code: null, status: null, params: {}, errorKey: 'save_unverified' }
    return {
      session: with_(session, { state: 'UNCERTAIN', pending: { ...pending, status: 'UNCERTAIN', resendable: true }, failure }),
      ignored: false, confirmed: null, toView: false,
    }
  }
  // ACK: the only base advance. Frontmatter and text are the submitted ones; the draft stays as typed.
  const base: EditBase = {
    docId: confirmed.docId, revisionNo: confirmed.revisionNo, updatedAt: confirmed.updatedAt,
    frontmatter: pending.frontmatter, body: pending.command ? null : pending.body,
  }
  const sameAsSubmission = !pending.command && session.draft === pending.body
  const next = with_(session, {
    base, pending: null, failure: null, remoteChanged: session.remoteChanged,
    state: sameAsSubmission ? 'VIEW' : 'DIRTY',
  })
  return { session: next, ignored: false, confirmed, toView: sameAsSubmission }
}

/**
 * A structured ACK keeps the draft fields (the window shows them); the caller says whether they still equal the
 * command it sent. Equal -> CLEAN (no unsaved difference), different -> DIRTY on the confirmed base.
 */
export function structuredAcknowledged(session: EditSession, stillEqual: boolean): EditSession {
  if (session.state === 'VIEW' && stillEqual) return with_(session, { state: 'CLEAN' })
  return with_(session, { state: stillEqual ? 'CLEAN' : 'DIRTY' })
}

/** A failed answer for the request of `key`. */
export function saveFailed(session: EditSession, key: string, context: DocumentEditTarget | null, error: CardApiError): SaveOutcome {
  if (!own(session, key, context) || !session.pending) return { session, ignored: true, confirmed: null, toView: false }
  const pending = session.pending
  const failure = classifyFailure(error)
  const keep = { code: failure.code, status: failure.status, params: failure.params, errorKey: failure.errorKey }
  const settle = (state: EditState, patch: Partial<EditSession> = {}, status: PendingStatus = 'REJECTED', resendable = false): SaveOutcome => ({
    session: with_(session, { state, failure: keep, pending: { ...pending, status, resendable }, ...patch }),
    ignored: false, confirmed: null, toView: false,
  })
  if (failure.kind === 'uncertain') return settle('UNCERTAIN', {}, 'UNCERTAIN', true)
  const code = failure.code
  if (code === 'revision_conflict') return settle('CONFLICT')
  if (code === 'slot_document_changed' || code === 'no_document') return settle('DETACHED')
  if (code === 'unauthorized') return settle('REJECTED', {}, 'REJECTED', true) // same pending after re-authentication
  if (code === 'token_kind_not_allowed') return settle('REJECTED', { configBlocked: true })
  if (code === 'idempotency_key_reused') return settle('REJECTED', { needsNewIntent: true })
  if (code === 'not_editable' && failure.errorKey === 'not_editable_body_unreadable') return settle('BLOCKED', { blockReason: 'body_unreadable' })
  return settle('REJECTED')
}

/** A confirmed re-authentication by the same actor: the 401-rejected pending may be sent again as it is. */
export function reauthenticated(session: EditSession, sameActor: boolean): EditSession {
  const pending = session.pending
  if (!pending || pending.status !== 'REJECTED' || !pending.resendable) return session
  // A different actor never reuses the old key: the intent must be started again.
  return sameActor ? session : with_(session, { pending: { ...pending, resendable: false }, needsNewIntent: true })
}

/** The call/auth setting was corrected after a 403 token_kind_not_allowed (explicit). */
export function configCorrected(session: EditSession): EditSession {
  return session.configBlocked ? with_(session, { configBlocked: false, state: session.state === 'REJECTED' ? 'DIRTY' : session.state }) : session
}

/** REJECTED -> DIRTY: the user starts a new intent (draft kept, old pending dropped, new key at the next send). */
export function startNewIntent(session: EditSession): EditSession {
  if (session.state !== 'REJECTED' || !session.base) return session
  return with_(session, { state: 'DIRTY', pending: null, failure: null, needsNewIntent: false })
}

/** CONFLICT: the latest document was read separately (latest only; base / draft / pending stay). */
export function conflictLatestRead(session: EditSession, snapshot: CardDocumentSnapshot): EditSession {
  return session.state === 'CONFLICT' || session.state === 'DETACHED' || isEditBlocked(session) ? with_(session, { latest: snapshot }) : latestRead(session, snapshot)
}

/**
 * BLOCKED after the editor had a base (e.g. a save answered body_unreadable): the draft and the rejected request are kept.
 * A BLOCKED session without a base is an initial read that failed and holds nothing of the user's.
 */
export function isEditBlocked(session: EditSession): boolean {
  return session.state === 'BLOCKED' && session.base !== null
}

/**
 * CONFLICT -> DIRTY: the user adopted a compared / merged text. The new base is the latest document's own
 * frontmatter, identity and revision (never the old frontmatter on a new id), the text is the merged draft.
 */
export function adoptMerge(session: EditSession, merged: string): EditSession {
  const latest = session.latest
  if ((session.state !== 'CONFLICT' && !isEditBlocked(session)) || !latest || latest.bodyState !== 'readable' || typeof latest.content !== 'string') return session
  if (session.targetDocId !== null && latest.docId !== session.targetDocId) return session
  const parts = splitFrontmatter(latest.content)
  return with_(session, {
    state: 'DIRTY', draft: merged, pending: null, failure: null, remoteChanged: false, needsNewIntent: false,
    base: { docId: latest.docId, revisionNo: latest.revisionNo, updatedAt: latest.updatedAt, frontmatter: parts.frontmatter, body: parts.body },
  })
}

/** Any editing state -> VIEW by an explicit discard (the caller already asked about unsaved text). */
export function discard(session: EditSession): EditSession {
  return with_(newSession(), { readSequence: session.readSequence + 1 })
}

/** Unsaved or unresolved text exists: the discard question applies (a pending uncertain request counts). */
export function hasUnsaved(session: EditSession): boolean {
  if (session.state === 'VIEW' || session.state === 'LOADING_BASE') return false
  // An initial read that failed holds nothing; a BLOCKED editor that has a base keeps its draft / rejected request.
  if (session.state === 'BLOCKED' && !session.base) return false
  if (session.pending && session.pending.status !== 'ACKNOWLEDGED') return true
  return !!session.base && session.base.body !== null && session.draft !== session.base.body
}

/** The new-save lock other windows consult: an unresolved request of this document (single_inflight across IO). */
export function hasUnresolvedRequest(session: EditSession): boolean {
  return !!session.pending && (session.pending.status === 'INFLIGHT' || session.pending.status === 'UNCERTAIN')
}
