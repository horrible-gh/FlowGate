// flowgate.v02.0127 T#5 (0009-L §2.15.3 / §3.1) — the TS card's v2 revisions: raw text, cases and the brief.
// Every draft (the raw text, a case, the brief) pins its own base snapshot `{docId, revisionNo, ctx}` when its edit
// starts; a save sends that base with a frozen request and never refreshes the revision just before sending. The
// answer is judged by the shared session model (cardEditSession): only this request's confirmed SAVED / REPLAY
// advances the saving draft's base (to the confirmed N+1); another draft keeps its own base and meets a conflict
// instead of a silent merge. 401 / 403 token_kind_not_allowed are REJECTED (never uncertain); network / 5xx /
// a malformed answer is UNCERTAIN, and the document then keeps that one frozen request — an explicit retry by
// the same draft resends it as it is (same key, same bytes) and nothing else may be sent for the document.
//
// Review fixes (rej_01M6JYV3MWMJJWMK):
// - the document's one request is claimed in the shared registry (documentIoTsBegin) from INFLIGHT on, so uploads and
//   document windows see it, and a TS request is refused while an upload / window / another TS owner holds the document;
// - the uncertain lock carries its owner: only the owner that set it clears it;
// - a retry is sent with the ORIGINAL pending context (card coordinates), and the owner includes those coordinates,
//   so another card cannot take the request over;
// - an ACK carries the confirmed pending's submitted text / command: the caller adopts exactly that as the new base;
// - 401 keeps the original request for a same-actor resend, 403 token_kind_not_allowed and idempotency_key_reused
//   stop further sending until the setting is corrected / a new intent is started explicitly.
import {
  cardDocumentContextOf,
  cardDocumentErrorOf,
  cardDocumentRevise,
  type CardApiError,
  type CardDocumentContext,
  type CardRevisionConfirmed,
  type CardStructuredCommand,
} from '@/features/document'
import {
  beginSave,
  newIdempotencyKey,
  newSession,
  requestOf,
  saveAnswered,
  saveFailed,
  type EditSession,
  type PendingRequest,
  type SaveFailure,
} from '@/features/document/cardEditSession'
import { documentIoMarkUnresolved, documentIoTsBegin, documentIoTsEnd } from '@/features/documentWindow'

/** The base a draft pinned at its start. `revisionNo` only moves with this draft's own confirmed ACK. */
export interface TsBase {
  readonly docId: string
  readonly revisionNo: number
  /** The card coordinates of the snapshot the base came from; a save and its retry use these only. */
  readonly ctx: CardDocumentContext
}

/** What is saved: the whole stored text, or a structured command (cases/brief). */
export type TsPayload = { readonly raw: string } | { readonly command: CardStructuredCommand }

/** What the confirmed request submitted (the pending's own bytes): the only text the new base may adopt. */
export interface TsSubmitted {
  readonly text: string | null
  readonly command: CardStructuredCommand | null
}

export type TsReviseResult =
  | { readonly ok: true; readonly confirmed: CardRevisionConfirmed; readonly submitted: TsSubmitted; readonly resent: boolean }
  | { readonly ok: false; readonly kind: 'rejected' | 'uncertain' | 'busy' | 'no_context' | 'blocked'; readonly failure: SaveFailure | null; readonly error: CardApiError | null }

/** `config`: nothing is sent until the call setting is corrected (403); `new_intent`: until a new intent starts (key reuse). */
type HeldBlock = null | 'config' | 'new_intent'

interface Held {
  readonly owner: string
  readonly pending: PendingRequest
  /** 'uncertain' (resend as it is) or 'unauthorized' (a 401 the same actor resends after re-authentication). */
  readonly why: 'uncertain' | 'unauthorized'
  readonly block: HeldBlock
  readonly failure: SaveFailure | null
}

/** The one kept request of each document: single_inflight across raw / cases / brief / upload / window. */
const held = new Map<string, Held>()

/** A TS request is kept for the document (the shared registry answers windows and uploads for in-flight and kept ones). */
export function tsHasUnresolved(docId: string): boolean {
  return held.has(docId)
}

export function tsHeldOwner(docId: string): string | null {
  return held.get(docId)?.owner ?? null
}

export function resetTsEditSessions(): void {
  for (const [docId, entry] of held) documentIoMarkUnresolved(docId, false, entry.owner)
  held.clear()
}

/** The owner of a draft: kind + the card coordinates + the document, so another card never takes the request over. */
export function tsOwnerOf(kind: 'raw' | 'brief' | 'cases', ctx: CardDocumentContext, docId: string): string {
  return `${kind}:${ctx.projectId}/${ctx.flowId}/${ctx.nodeKey}/${ctx.ownerSlot}:${docId}`
}

function sessionOf(ctx: CardDocumentContext, base: { docId: string; revisionNo: number }): EditSession {
  return {
    ...newSession(),
    state: 'DIRTY',
    context: ctx,
    targetDocId: base.docId,
    base: { docId: base.docId, revisionNo: base.revisionNo, updatedAt: null, frontmatter: '', body: null },
    draft: '',
  }
}

/** After a 403 token_kind_not_allowed the call setting was corrected: the kept request may be sent again (explicit). */
export function tsConfigCorrected(docId: string, owner: string): void {
  const entry = held.get(docId)
  if (entry && entry.owner === owner && entry.block === 'config') held.set(docId, { ...entry, block: null })
}

/** An explicit new intent (after idempotency_key_reused, or a discarded request): the old pending is dropped, the next send uses a new key. */
export function tsStartNewIntent(docId: string, owner: string): void {
  const entry = held.get(docId)
  if (!entry || entry.owner !== owner) return
  held.delete(docId)
  documentIoMarkUnresolved(docId, false, owner)
}

/** The user re-authenticated: the same actor resends the 401-rejected request as it is; another actor must start a new intent. */
export function tsReauthenticated(docId: string, owner: string, sameActor: boolean): void {
  const entry = held.get(docId)
  if (!entry || entry.owner !== owner || entry.why !== 'unauthorized') return
  if (!sameActor) tsStartNewIntent(docId, owner)
}

/**
 * Sends one TS revision for `owner` (a draft's identity). The request is frozen from the draft's pinned `base`
 * (including its card coordinates); a document whose one request belongs to anyone else is busy, and the owner of
 * the kept request resends exactly that request with its original context (the payload argument is then ignored).
 */
export async function reviseTs(owner: string, base: TsBase | null, payload: TsPayload): Promise<TsReviseResult> {
  if (!base?.ctx) return { ok: false, kind: 'no_context', failure: null, error: null }
  const docId = base.docId
  const holding = held.get(docId)
  if (holding && holding.owner !== owner) return { ok: false, kind: 'busy', failure: null, error: null }
  if (holding?.block) return { ok: false, kind: 'blocked', failure: holding.failure, error: null }
  // INFLIGHT is registered in the shared registry before anything is sent; an upload / window / other TS request refuses it.
  if (!documentIoTsBegin(docId, owner)) return { ok: false, kind: 'busy', failure: null, error: null }
  let session: EditSession
  let pending: PendingRequest
  // flowgate.v02.0157: the shared session also edits standalone documents; a TS request is always a card's (its
  // coordinates come from the card's snapshot), so the card coordinates are carried as such.
  let ctx: CardDocumentContext
  const resent = !!holding
  if (holding) {
    // Explicit retry of the kept request: the frozen pending as it is, sent with its own original coordinates.
    const kept = cardDocumentContextOf(holding.pending.context)
    if (kept === null) {
      documentIoTsEnd(docId, owner)
      return { ok: false, kind: 'no_context', failure: null, error: null }
    }
    ctx = kept
    session = { ...sessionOf(ctx, { docId: holding.pending.expectedDocId, revisionNo: holding.pending.expectedRevision }), state: 'UNCERTAIN', pending: holding.pending }
    const again = beginSave(session, newIdempotencyKey, holding.pending.command)
    if (again.request === null) {
      documentIoTsEnd(docId, owner)
      return { ok: false, kind: 'busy', failure: null, error: null }
    }
    session = again.session
    pending = again.request
  } else {
    ctx = base.ctx
    const command = 'command' in payload ? payload.command : null
    // The structured / raw difference lives in the frozen request; raw text rides in `body` with an empty frontmatter.
    const start = { ...sessionOf(base.ctx, base), draft: 'raw' in payload ? payload.raw : '' }
    const started = beginSave(
      command === null ? { ...start, base: { ...start.base!, body: '' } } : start,
      newIdempotencyKey,
      command,
    )
    if (started.request === null) {
      documentIoTsEnd(docId, owner)
      return { ok: false, kind: 'busy', failure: null, error: null }
    }
    session = started.session
    pending = started.request
  }
  try {
    let error: CardApiError | null = null
    let outcome
    try {
      const raw = await cardDocumentRevise(ctx, requestOf(pending))
      outcome = saveAnswered(session, pending.key, pending.context, raw)
    } catch (thrown) {
      error = cardDocumentErrorOf(thrown)
      outcome = saveFailed(session, pending.key, pending.context, error)
    }
    if (outcome.confirmed) {
      held.delete(docId)
      documentIoMarkUnresolved(docId, false, owner)
      return {
        ok: true, confirmed: outcome.confirmed, resent,
        submitted: { text: pending.command ? null : pending.text, command: pending.command },
      }
    }
    return settleFailure(owner, docId, outcome.session, error)
  } finally {
    documentIoTsEnd(docId, owner)
  }
}

function settleFailure(owner: string, docId: string, session: EditSession, error: CardApiError | null): TsReviseResult {
  const pending = session.pending
  const failure = session.failure
  if (session.state === 'UNCERTAIN' && pending) {
    held.set(docId, { owner, pending, why: 'uncertain', block: null, failure })
    documentIoMarkUnresolved(docId, true, owner)
    return { ok: false, kind: 'uncertain', failure, error }
  }
  // §2.12: the original request and its owner stay for the rejections that are resolved outside the draft.
  if (pending && failure?.code === 'unauthorized') {
    held.set(docId, { owner, pending, why: 'unauthorized', block: null, failure })
    documentIoMarkUnresolved(docId, true, owner)
    return { ok: false, kind: 'rejected', failure, error }
  }
  if (pending && (failure?.code === 'token_kind_not_allowed' || failure?.code === 'idempotency_key_reused')) {
    held.set(docId, { owner, pending, why: 'uncertain', block: failure.code === 'token_kind_not_allowed' ? 'config' : 'new_intent', failure })
    documentIoMarkUnresolved(docId, true, owner)
    return { ok: false, kind: 'rejected', failure, error }
  }
  // Any other pre-commit rejection leaves nothing to resend by key: the draft starts a new intent (new key) when saved again.
  held.delete(docId)
  documentIoMarkUnresolved(docId, false, owner)
  return { ok: false, kind: 'rejected', failure, error }
}
