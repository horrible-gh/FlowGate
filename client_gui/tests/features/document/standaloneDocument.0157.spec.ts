/**
 * flowgate.v02.0157 — a standalone document (no card line holds it) is edited by its doc_id. The transport reads
 * GET /api/v2/documents/{doc_id} (snapshot + edit_path) and saves POST …/revisions { expected_revision, content,
 * idempotency_key }; the session adopts a base only for edit_path=non_card and confirms a save without a card line.
 * A card target keeps its own route and its line check unchanged.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
}))

import {
  cardDocumentContextOf,
  cardDocumentIsStandalone,
  cardDocumentStandaloneConfirmRevision,
  cardDocumentStandaloneRead,
  cardDocumentStandaloneRevise,
  cardDocumentStandaloneTarget,
  cardDocumentTargetConfirmRevision,
  type CardApiError,
  type CardDocumentContext,
  type CardDocumentSnapshot,
} from '@/features/document'
import {
  baseRead,
  beginLoad,
  beginSave,
  newSession,
  requestOf,
  saveAnswered,
  saveFailed,
  typeDraft,
  type EditSession,
} from '@/features/document/cardEditSession'

const DOC = 'flowgate.v02.0157.0003-R'
const TARGET = cardDocumentStandaloneTarget(DOC)
const CARD: CardDocumentContext = { projectId: 'flowgate', flowId: 'flowgate.v02.0157', nodeKey: 'req', ownerSlot: 'instruction', openedDocId: DOC }
const FRONT = '---\nproject: flowgate\n---'
const snap = (editPath: string | undefined, revisionNo = 3, content: string | null = `${FRONT}\n본문`): CardDocumentSnapshot =>
  ({ docId: DOC, revisionNo, updatedAt: 't', content, bodyState: content === null ? 'file_path_missing' : 'readable', line: null, ...(editPath ? { editPath } : {}) })
const standaloneOk = (revision: number, extra: Record<string, unknown> = {}) =>
  ({ ok: true, server_ts: 't', doc_id: DOC, revision_no: revision, updated_at: 'u', replayed: false, edit_path: 'non_card', ...extra })
const api = (status: number | null, code: string | null = null): CardApiError => ({ status, code, params: {}, legacyDetail: null, network: false })

/** `null`: the read carried no edit_path at all. */
function loaded(editPath: string | null = 'non_card'): EditSession {
  const started = beginLoad(newSession(), TARGET, DOC)
  return baseRead(started.session, started.seq, snap(editPath ?? undefined))
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('transport', () => {
  it('a target is the doc_id alone; card coordinates are told apart', () => {
    expect(TARGET).toEqual({ kind: 'document', docId: DOC })
    expect(cardDocumentIsStandalone(TARGET)).toBe(true)
    expect(cardDocumentIsStandalone(CARD)).toBe(false)
    expect(cardDocumentIsStandalone(null)).toBe(false)
    expect(cardDocumentContextOf(CARD)).toBe(CARD)
    expect(cardDocumentContextOf(TARGET)).toBeNull()
  })

  it('reads GET /api/v2/documents/{doc_id} with the server classification', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, doc_id: DOC, revision_no: 3, updated_at: 't', body_state: 'readable', content: 'x', edit_path: 'non_card' } })
    const snapshot = await cardDocumentStandaloneRead(TARGET)
    expect(getRequest).toHaveBeenCalledWith(`/api/v2/documents/${encodeURIComponent(DOC)}`)
    expect(snapshot).toMatchObject({ docId: DOC, revisionNo: 3, content: 'x', bodyState: 'readable', editPath: 'non_card', line: null })
  })

  it('saves POST …/revisions with expected_revision, content and key only (no card coordinates)', async () => {
    postRequest.mockResolvedValue({ data: standaloneOk(4) })
    const raw = await cardDocumentStandaloneRevise(TARGET, { expectedDocId: DOC, expectedRevision: 3, idempotencyKey: 'k-1', content: 'body' })
    expect(postRequest).toHaveBeenCalledWith(`/api/v2/documents/${encodeURIComponent(DOC)}/revisions`, { expected_revision: 3, content: 'body', idempotency_key: 'k-1' })
    expect(raw).toEqual(standaloneOk(4))
    await expect(cardDocumentStandaloneRevise(TARGET, { expectedDocId: DOC, expectedRevision: 3, idempotencyKey: 'k', command: { kind: 'ts_brief', text: 'x' } }))
      .rejects.toThrow()
    expect(postRequest).toHaveBeenCalledTimes(1)
  })

  it('a standalone success needs edit_path=non_card instead of a line; the card check is unchanged', () => {
    expect(cardDocumentStandaloneConfirmRevision(standaloneOk(4), DOC, 3)).toEqual({ docId: DOC, revisionNo: 4, updatedAt: 'u', replayed: false, line: null })
    expect(cardDocumentStandaloneConfirmRevision(standaloneOk(4, { edit_path: 'card' }), DOC, 3)).toBeNull()
    expect(cardDocumentStandaloneConfirmRevision(standaloneOk(5), DOC, 3)).toBeNull()
    expect(cardDocumentStandaloneConfirmRevision({ ...standaloneOk(4), doc_id: 'other' }, DOC, 3)).toBeNull()
    expect(cardDocumentStandaloneConfirmRevision({ ...standaloneOk(4), ok: false }, DOC, 3)).toBeNull()
    // The target picks the check: a card answer still needs its line.
    expect(cardDocumentTargetConfirmRevision(CARD, standaloneOk(4), DOC, 3)).toBeNull()
    expect(cardDocumentTargetConfirmRevision(CARD, { ...standaloneOk(4), line: { slot: 'instruction' } }, DOC, 3)).toMatchObject({ revisionNo: 4 })
    expect(cardDocumentTargetConfirmRevision(TARGET, standaloneOk(4), DOC, 3)).toMatchObject({ revisionNo: 4, line: null })
  })
})

describe('session', () => {
  it('edit_path=non_card adopts text, revision and doc_id as CLEAN', () => {
    const session = loaded()
    expect(session.state).toBe('CLEAN')
    expect(session.context).toBe(TARGET)
    expect(session.base).toMatchObject({ docId: DOC, revisionNo: 3, frontmatter: FRONT, body: '본문' })
  })

  it.each([['card'], ['unavailable'], [null]])('edit_path=%s never becomes a base: BLOCKED binding_required', (editPath) => {
    const session = loaded(editPath)
    expect(session).toMatchObject({ state: 'BLOCKED', blockReason: 'binding_required', base: null })
    expect(beginSave(session, () => 'k').request).toBeNull()
  })

  it('a save freezes the same target; its own non_card answer is the confirmed N+1 and returns to the view', () => {
    const started = beginSave(typeDraft(loaded(), '고친 본문'), () => 'key-1')
    const pending = started.request!
    expect(pending.context).toBe(TARGET)
    expect(requestOf(pending)).toEqual({ expectedDocId: DOC, expectedRevision: 3, idempotencyKey: 'key-1', content: `${FRONT}\n고친 본문` })
    const outcome = saveAnswered(started.session, pending.key, TARGET, standaloneOk(4))
    expect(outcome.confirmed).toMatchObject({ docId: DOC, revisionNo: 4, line: null })
    expect(outcome.toView).toBe(true)
    expect(outcome.session.base).toMatchObject({ revisionNo: 4, body: '고친 본문' })
  })

  it('an answer without edit_path=non_card is uncertain (same request resent, never a new key)', () => {
    const started = beginSave(typeDraft(loaded(), 'x'), () => 'key-1')
    const outcome = saveAnswered(started.session, 'key-1', TARGET, { ...standaloneOk(4), edit_path: undefined, line: {} })
    expect(outcome.session.state).toBe('UNCERTAIN')
    const again = beginSave(outcome.session, () => 'key-2')
    expect(again.request).toMatchObject({ key: 'key-1', text: `${FRONT}\nx` })
  })

  it('an answer for another target object is ignored', () => {
    const started = beginSave(typeDraft(loaded(), 'x'), () => 'key-1')
    expect(saveAnswered(started.session, 'key-1', cardDocumentStandaloneTarget(DOC), standaloneOk(4)).ignored).toBe(true)
  })

  it('409 is a conflict, card_binding_missing (bound to a card meanwhile) is rejected with its reason', () => {
    const first = beginSave(typeDraft(loaded(), 'x'), () => 'key-1')
    expect(saveFailed(first.session, 'key-1', TARGET, api(409, 'revision_conflict')).session.state).toBe('CONFLICT')
    const second = beginSave(typeDraft(loaded(), 'x'), () => 'key-2')
    const bound = saveFailed(second.session, 'key-2', TARGET, api(422, 'card_binding_missing')).session
    expect(bound).toMatchObject({ state: 'REJECTED', failure: { errorKey: 'card_binding_missing' } })
    expect(bound.draft).toBe('x')
  })

  it('structured (TS) commands are card-only', () => {
    const session = typeDraft(loaded(), 'x')
    expect(beginSave(session, () => 'k', { kind: 'ts_brief', text: 'x' }).blocked).toBe('not_editable')
  })

  it('a card session keeps its own coordinates and line check', () => {
    const started = beginLoad(newSession(), CARD, DOC)
    const session = baseRead(started.session, started.seq, { ...snap(undefined), line: {} })
    expect(session.state).toBe('CLEAN')
    const saving = beginSave(typeDraft(session, 'x'), () => 'key-1')
    expect(saveAnswered(saving.session, 'key-1', CARD, standaloneOk(4)).session.state).toBe('UNCERTAIN')
  })
})
