/**
 * flowgate.v02.0127 T#5 — the client editing session of a card document (0009-L §2.12 / §2.13 / §3.1).
 * The transitions of the table are exercised one by one on the pure model: the own-ACK base advance, REJECTED for
 * 401 / 403 token_kind_not_allowed, no automatic rebase and no new key for an uncertain request.
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: vi.fn() }))

import {
  cardDocumentErrorOf,
  type CardApiError,
  type CardDocumentContext,
  type CardDocumentSnapshot,
} from '@/features/document'
import {
  adoptMerge,
  baseRead,
  beginLoad,
  beginSave,
  classifyFailure,
  conflictLatestRead,
  discard,
  externalEvent,
  hasUnresolvedRequest,
  hasUnsaved,
  isEditBlocked,
  latestRead,
  newIdempotencyKey,
  newSession,
  reauthenticated,
  requestOf,
  saveAnswered,
  saveFailed,
  startNewIntent,
  typeDraft,
  type EditSession,
} from '@/features/document/cardEditSession'

const DOC = 'flowgate.v02.0127.0007-N'
const CTX: CardDocumentContext = { projectId: 'flowgate', flowId: 'flowgate.v02.0127', nodeKey: 'research', ownerSlot: 'instruction', openedDocId: DOC }
const snap = (revisionNo: number, content: string | null = '---\nproject: flowgate\n---\n본문', docId = DOC, bodyState = 'readable'): CardDocumentSnapshot =>
  ({ docId, revisionNo, updatedAt: 't' + revisionNo, content, bodyState, line: {} })
const api = (status: number | null, code: string | null = null, extra: Partial<CardApiError> = {}): CardApiError =>
  ({ status, code, params: {}, legacyDetail: null, network: false, ...extra })
const okBody = (docId: string, revision: number, replayed = false) =>
  ({ ok: true, server_ts: 't', doc_id: docId, revision_no: revision, updated_at: 'u', replayed, line: { slot: 'instruction' } })

function clean(revision = 5): EditSession {
  const started = beginLoad(newSession(), CTX, DOC)
  return baseRead(started.session, started.seq, snap(revision))
}
function saving(session: EditSession, key = 'key-1') {
  const started = beginSave(session, () => key)
  expect(started.request).not.toBeNull()
  return { session: started.session, request: started.request! }
}

describe('LOADING_BASE (§3.1 rows 1~3)', () => {
  it('adopts text, revision and doc_id together as CLEAN and splits the kept frontmatter', () => {
    const session = clean()
    expect(session.state).toBe('CLEAN')
    expect(session.base).toMatchObject({ docId: DOC, revisionNo: 5, frontmatter: '---\nproject: flowgate\n---', body: '본문' })
    expect(session.draft).toBe('본문')
  })

  it('an empty string is a readable body, never null', () => {
    const started = beginLoad(newSession(), CTX, DOC)
    const session = baseRead(started.session, started.seq, snap(1, ''))
    expect(session.state).toBe('CLEAN')
    expect(session.base?.body).toBe('')
  })

  it('no card context blocks editing: there is no v1 fallback', () => {
    const { session } = beginLoad(newSession(), null, DOC)
    expect(session).toMatchObject({ state: 'BLOCKED', blockReason: 'binding_required' })
    expect(beginSave(session, () => 'k').blocked).toBe('blocked')
  })

  it('an unreadable body blocks and never becomes an empty string', () => {
    const started = beginLoad(newSession(), CTX, DOC)
    const session = baseRead(started.session, started.seq, snap(2, null, DOC, 'read_error'))
    expect(session).toMatchObject({ state: 'BLOCKED', blockReason: 'read_error' })
    expect(session.base).toBeNull()
  })

  it('a read of another document is DETACHED and is not adopted as the save base', () => {
    const started = beginLoad(newSession(), CTX, DOC)
    const session = baseRead(started.session, started.seq, snap(1, 'x', 'flowgate.v02.0127.0099-N'))
    expect(session.state).toBe('DETACHED')
    expect(session.base).toBeNull()
  })

  it('a stale read sequence is dropped', () => {
    const first = beginLoad(newSession(), CTX, DOC)
    const second = beginLoad(first.session, CTX, DOC)
    expect(baseRead(second.session, first.seq, snap(9))).toBe(second.session)
  })
})

describe('CLEAN / DIRTY and external changes', () => {
  it('typing makes DIRTY against the base and typing it back makes CLEAN', () => {
    let session = typeDraft(clean(), '본문 수정')
    expect(session.state).toBe('DIRTY')
    session = typeDraft(session, '본문')
    expect(session.state).toBe('CLEAN')
  })

  it('an event, a later read or a conflict answer never changes draft / base / pending', () => {
    const dirty = typeDraft(clean(5), '내 초안')
    const busy = saving(dirty).session
    for (const next of [externalEvent(dirty), latestRead(dirty, snap(9, 'other')), externalEvent(busy)]) {
      expect(next.draft).toBe(dirty.draft)
      expect(next.base).toBe(dirty.base)
    }
    const read = latestRead(dirty, snap(9, 'other'))
    expect(read.remoteChanged).toBe(true)
    expect(read.latest?.revisionNo).toBe(9)
    expect(externalEvent(busy).pending).toBe(busy.pending)
  })
})

describe('SAVING (§3.1 rows 5~7, single_inflight = 1)', () => {
  it('freezes the original request: base target, submitted text and a UUID v4 key', () => {
    const { request } = saving(typeDraft(clean(5), '본문 2'), newIdempotencyKey())
    expect(request).toMatchObject({ expectedDocId: DOC, expectedRevision: 5, text: '---\nproject: flowgate\n---\n본문 2', status: 'INFLIGHT' })
    expect(request.key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(requestOf(request)).toEqual({ expectedDocId: DOC, expectedRevision: 5, idempotencyKey: request.key, content: request.text })
  })

  it('a second save while SAVING is refused', () => {
    const busy = saving(typeDraft(clean(), 'x')).session
    expect(beginSave(busy, () => 'k2')).toMatchObject({ request: null, blocked: 'saving' })
  })

  it('own SAVED with an unchanged draft returns to VIEW with the base moved to the response doc_id / N+1 / submission', () => {
    const { session, request } = saving(typeDraft(clean(5), '새 본문'))
    const outcome = saveAnswered(session, request.key, CTX, okBody(DOC, 6))
    expect(outcome).toMatchObject({ ignored: false, toView: true })
    expect(outcome.session.state).toBe('VIEW')
    expect(outcome.session.base).toMatchObject({ docId: DOC, revisionNo: 6, body: '새 본문', frontmatter: '---\nproject: flowgate\n---' })
    expect(outcome.session.pending).toBeNull()
  })

  it('own REPLAY is the same ACK (it points at the same N+1)', () => {
    const { session, request } = saving(typeDraft(clean(5), '새 본문'))
    const outcome = saveAnswered(session, request.key, CTX, okBody(DOC, 6, true))
    expect(outcome.confirmed).toMatchObject({ revisionNo: 6, replayed: true })
    expect(outcome.session.base?.revisionNo).toBe(6)
  })

  it('own SAVED with a draft typed meanwhile is DIRTY on the confirmed base, the draft kept, next save is N+1 with a new key', () => {
    const { session, request } = saving(typeDraft(clean(5), '제출본'))
    const typed = typeDraft(session, '제출본 + 더 입력')
    const outcome = saveAnswered(typed, request.key, CTX, okBody(DOC, 6))
    expect(outcome.toView).toBe(false)
    expect(outcome.session.state).toBe('DIRTY')
    expect(outcome.session.draft).toBe('제출본 + 더 입력')
    expect(outcome.session.base).toMatchObject({ revisionNo: 6, body: '제출본' })
    const next = beginSave(outcome.session, () => 'key-2')
    expect(next.request).toMatchObject({ expectedRevision: 6, key: 'key-2', body: '제출본 + 더 입력' })
    expect(next.request!.key).not.toBe(request.key)
  })

  it('a later GET reporting N+2 is latest only: the ACK base is not replaced again', () => {
    const { session, request } = saving(typeDraft(clean(5), 'a'))
    const acked = saveAnswered(typeDraft(session, 'a2'), request.key, CTX, okBody(DOC, 6)).session
    const later = latestRead(acked, snap(7, 'zzz'))
    expect(later.base?.revisionNo).toBe(6)
    expect(later.latest?.revisionNo).toBe(7)
  })

  it('an answer of another window / another pending / no pending is ignored', () => {
    const { session, request } = saving(typeDraft(clean(), 'x'))
    expect(saveAnswered(session, 'other-key', CTX, okBody(DOC, 6)).ignored).toBe(true)
    expect(saveAnswered(session, request.key, { ...CTX }, okBody(DOC, 6)).ignored).toBe(true)
    expect(saveAnswered(clean(), request.key, CTX, okBody(DOC, 6)).ignored).toBe(true)
    expect(saveFailed(session, 'other-key', CTX, api(409, 'revision_conflict')).ignored).toBe(true)
  })

  it('a damaged or mismatching success is UNCERTAIN, never an ACK', () => {
    const { session, request } = saving(typeDraft(clean(5), 'x'))
    const bad = [{}, { ...okBody(DOC, 6), ok: false }, okBody(DOC, 7), okBody('flowgate.v02.0127.0099-N', 6),
      { ...okBody(DOC, 6), replayed: 'yes' }, { ...okBody(DOC, 6), updated_at: undefined }, { ...okBody(DOC, 6), line: undefined }, null]
    for (const raw of bad) {
      const outcome = saveAnswered(session, request.key, CTX, raw)
      expect(outcome.confirmed).toBeNull()
      expect(outcome.session.state).toBe('UNCERTAIN')
      expect(outcome.session.base?.revisionNo).toBe(5)
      expect(outcome.session.pending).toMatchObject({ status: 'UNCERTAIN', resendable: true })
    }
  })
})

describe('REJECTED: 401 / 403 token_kind_not_allowed (§3.1 rows 8~10)', () => {
  it('401 unauthorized is REJECTED with draft, base and the original request kept; no UNCERTAIN', () => {
    const { session, request } = saving(typeDraft(clean(5), '초안'))
    const outcome = saveFailed(session, request.key, CTX, api(401, null))
    expect(outcome.session.state).toBe('REJECTED')
    expect(outcome.session.failure).toMatchObject({ code: 'unauthorized', errorKey: 'unauthorized' })
    expect(outcome.session.draft).toBe('초안')
    expect(outcome.session.base?.revisionNo).toBe(5)
    expect(outcome.session.pending).toMatchObject({ status: 'REJECTED', key: request.key, resendable: true })
    expect(hasUnresolvedRequest(outcome.session)).toBe(false)
    expect(classifyFailure(api(401, 'unauthorized')).kind).toBe('rejected')
  })

  it('after re-authentication by the same actor the same pending is resent as it is (same key, same bytes)', () => {
    const { session, request } = saving(typeDraft(clean(5), '초안'))
    const rejected = saveFailed(session, request.key, CTX, api(401, 'unauthorized')).session
    const again = beginSave(reauthenticated(rejected, true), () => 'never-used')
    expect(again.request).toMatchObject({ key: request.key, text: request.text, expectedDocId: DOC, expectedRevision: 5, status: 'INFLIGHT' })
    expect(again.session.state).toBe('SAVING')
  })

  it('a different actor never reuses the old key: a new intent is required', () => {
    const { session, request } = saving(typeDraft(clean(5), '초안'))
    const rejected = saveFailed(session, request.key, CTX, api(401, 'unauthorized')).session
    const other = reauthenticated(rejected, false)
    expect(beginSave(other, () => 'k')).toMatchObject({ request: null, blocked: 'needs_new_intent' })
    const restarted = startNewIntent(other)
    expect(restarted).toMatchObject({ state: 'DIRTY', pending: null, draft: '초안' })
    expect(beginSave(restarted, () => 'fresh').request).toMatchObject({ key: 'fresh', expectedRevision: 5 })
  })

  it('403 token_kind_not_allowed is REJECTED and repeated sending stops until the setting is corrected', () => {
    const { session, request } = saving(typeDraft(clean(5), '초안'))
    const outcome = saveFailed(session, request.key, CTX, api(403, 'token_kind_not_allowed'))
    expect(outcome.session).toMatchObject({ state: 'REJECTED', configBlocked: true })
    expect(outcome.session.pending?.status).toBe('REJECTED')
    expect(beginSave(outcome.session, () => 'k')).toMatchObject({ request: null, blocked: 'config' })
  })

  it('invalid_content and locks are REJECTED; the user fixes the text and a new intent gets a new key', () => {
    const { session, request } = saving(typeDraft(clean(5), '잘못'))
    const rejected = saveFailed(session, request.key, CTX, api(422, 'invalid_content')).session
    expect(rejected.state).toBe('REJECTED')
    const fixed = typeDraft(rejected, '고침')
    const retry = beginSave(fixed, () => 'new-key')
    expect(retry.request).toMatchObject({ key: 'new-key', body: '고침', expectedRevision: 5 })
    expect(classifyFailure(api(423, 'GROUP_AI_RUN_LOCKED')).kind).toBe('rejected')
  })

  it('idempotency_key_reused never swaps the key by itself', () => {
    const { session, request } = saving(typeDraft(clean(5), 'x'))
    const rejected = saveFailed(session, request.key, CTX, api(409, 'idempotency_key_reused')).session
    expect(rejected.needsNewIntent).toBe(true)
    expect(beginSave(rejected, () => 'auto')).toMatchObject({ request: null, blocked: 'needs_new_intent' })
  })
})

describe('CONFLICT / DETACHED / UNCERTAIN (§3.1 rows 11~19)', () => {
  it('revision_conflict keeps draft / base / original request; latest is filled apart; no rebase', () => {
    const { session, request } = saving(typeDraft(clean(5), '내 초안'))
    const conflict = saveFailed(session, request.key, CTX, api(409, 'revision_conflict')).session
    expect(conflict.state).toBe('CONFLICT')
    expect(beginSave(conflict, () => 'k')).toMatchObject({ request: null, blocked: 'conflict' })
    const read = conflictLatestRead(conflict, snap(8, '---\nproject: flowgate\nrev: 8\n---\n최신'))
    expect(read.state).toBe('CONFLICT')
    expect(read.latest?.revisionNo).toBe(8)
    expect(read.base?.revisionNo).toBe(5)
    expect(read.draft).toBe('내 초안')
  })

  it('adopting a merged text uses the latest document frontmatter, id and revision as the new base and a new key later', () => {
    const { session, request } = saving(typeDraft(clean(5), '내 초안'))
    let conflict = saveFailed(session, request.key, CTX, api(409, 'revision_conflict')).session
    conflict = conflictLatestRead(conflict, snap(8, '---\nproject: flowgate\nrev: 8\n---\n최신'))
    const merged = adoptMerge(conflict, '병합본')
    expect(merged).toMatchObject({ state: 'DIRTY', draft: '병합본', pending: null })
    expect(merged.base).toMatchObject({ docId: DOC, revisionNo: 8, frontmatter: '---\nproject: flowgate\nrev: 8\n---', body: '최신' })
    expect(beginSave(merged, () => 'n').request).toMatchObject({ expectedRevision: 8, key: 'n', text: '---\nproject: flowgate\nrev: 8\n---\n병합본' })
  })

  it('adopting is refused without a readable latest of the same document', () => {
    const { session, request } = saving(typeDraft(clean(5), 'x'))
    const conflict = saveFailed(session, request.key, CTX, api(409, 'revision_conflict')).session
    expect(adoptMerge(conflict, 'm')).toBe(conflict)
    const other = conflictLatestRead(conflict, snap(8, 'z', 'flowgate.v02.0127.0099-N'))
    expect(adoptMerge(other, 'm')).toBe(other)
  })

  it('slot_document_changed / no_document are DETACHED: the original is kept, nothing is saved to another document', () => {
    for (const code of ['slot_document_changed', 'no_document']) {
      const { session, request } = saving(typeDraft(clean(5), '원문'))
      const detached = saveFailed(session, request.key, CTX, api(409, code)).session
      expect(detached.state).toBe('DETACHED')
      expect(detached.draft).toBe('원문')
      expect(beginSave(detached, () => 'k').blocked).toBe('detached')
    }
  })

  it('network / 500 / unregistered answers are UNCERTAIN and only the same request is resent, never a new key', () => {
    for (const error of [api(null, null, { network: true }), api(500, 'internal_error'), api(418, 'brand_new_code')]) {
      const { session, request } = saving(typeDraft(clean(5), '초안'))
      const uncertain = saveFailed(session, request.key, CTX, error).session
      expect(uncertain.state).toBe('UNCERTAIN')
      expect(hasUnresolvedRequest(uncertain)).toBe(true)
      expect(hasUnsaved(uncertain)).toBe(true)
      const typed = typeDraft(uncertain, '초안을 또 고침')
      const again = beginSave(typed, () => 'must-not-be-used')
      expect(again.request).toMatchObject({ key: request.key, text: request.text, body: '초안', expectedRevision: 5, status: 'INFLIGHT' })
    }
  })

  it('an explicit discard returns to VIEW and drops everything', () => {
    const { session } = saving(typeDraft(clean(5), 'x'))
    const view = discard(session)
    expect(view).toMatchObject({ state: 'VIEW', base: null, pending: null, draft: '' })
    expect(view.readSequence).toBeGreaterThan(session.readSequence)
  })
})

describe('structured commands and the unknown N+1 body', () => {
  it('a structured ACK moves the base to N+1 without inventing the full text; a raw save then needs its own snapshot', () => {
    const command = { kind: 'ts_brief', text: '새 지시' } as const
    const started = beginSave(typeDraft(clean(5), '본문'), () => 'cmd-key', command)
    expect(started.request).toMatchObject({ command, text: '', expectedRevision: 5 })
    expect(requestOf(started.request!)).toEqual({ expectedDocId: DOC, expectedRevision: 5, idempotencyKey: 'cmd-key', command })
    const outcome = saveAnswered(started.session, 'cmd-key', CTX, okBody(DOC, 6))
    expect(outcome.session.base).toMatchObject({ docId: DOC, revisionNo: 6, body: null })
    expect(beginSave(outcome.session, () => 'raw-key')).toMatchObject({ request: null, blocked: 'base_snapshot_required' })
    expect(beginSave(outcome.session, () => 'next-cmd', command).request).toMatchObject({ expectedRevision: 6, key: 'next-cmd' })
  })
})

describe('error reading (§2.13)', () => {
  it('reads v2 error.code / params and the v1 detail string', () => {
    const v2 = cardDocumentErrorOf({ isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'revision_conflict', params: { doc_id: DOC } } } } })
    expect(v2).toMatchObject({ status: 409, code: 'revision_conflict', params: { doc_id: DOC }, network: false })
    const v1 = cardDocumentErrorOf({ isAxiosError: true, response: { status: 409, data: { detail: 'card_document_edit_required' } } })
    expect(v1.legacyDetail).toBe('card_document_edit_required')
    const down = cardDocumentErrorOf({ isAxiosError: true })
    expect(down.network).toBe(true)
  })

  it('the v1 pointer is shown and rejected (no retry through v1); other 409 / 422 are not collapsed', () => {
    const v1 = classifyFailure(api(409, null, { legacyDetail: 'card_document_edit_required' }))
    expect(v1).toMatchObject({ kind: 'rejected', errorKey: 'card_document_edit_required' })
    expect(classifyFailure(api(409, 'revision_conflict')).errorKey).toBe('revision_conflict')
    expect(classifyFailure(api(422, 'not_editable', { params: { reason: 'final_approved' } })).errorKey).toBe('not_editable_final_approved')
    expect(classifyFailure(api(422, 'invalid_content')).errorKey).toBe('invalid_content')
  })
})

describe('body_unreadable on a save keeps the draft (review fix)', () => {
  const unreadable = () => saveFailed(saving(typeDraft(clean(5), 'unsaved')).session, 'key-1', CTX, api(422, 'not_editable', { params: { reason: 'body_unreadable' } })).session

  it('the blocked editor still holds the draft and the rejected request: discard protection stays', () => {
    const session = unreadable()
    expect(session).toMatchObject({ state: 'BLOCKED', draft: 'unsaved', blockReason: 'body_unreadable' })
    expect(session.pending?.status).toBe('REJECTED')
    expect(isEditBlocked(session)).toBe(true)
    expect(hasUnsaved(session)).toBe(true)
  })

  it('an initial read that failed holds nothing: no discard question', () => {
    const started = beginLoad(newSession(), CTX, DOC)
    const blocked = baseRead(started.session, started.seq, snap(5, null, DOC, 'file_path_missing'))
    expect(blocked.state).toBe('BLOCKED')
    expect(isEditBlocked(blocked)).toBe(false)
    expect(hasUnsaved(blocked)).toBe(false)
  })

  it('a later recovery read is kept as latest only; the draft stays until the user adopts the latest explicitly', () => {
    const read = conflictLatestRead(unreadable(), snap(7, '---
project: flowgate
rev: 7
---
복구된 본문'))
    expect(read).toMatchObject({ state: 'BLOCKED', draft: 'unsaved' })
    expect(read.base?.revisionNo).toBe(5)
    expect(read.latest?.revisionNo).toBe(7)
    const adopted = adoptMerge(read, 'unsaved')
    expect(adopted).toMatchObject({ state: 'DIRTY', draft: 'unsaved', pending: null })
    expect(adopted.base).toMatchObject({ revisionNo: 7, frontmatter: '---
project: flowgate
rev: 7
---' })
  })
})
