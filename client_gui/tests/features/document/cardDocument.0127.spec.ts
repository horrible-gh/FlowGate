/**
 * flowgate.v02.0127 T#5 — the card document transport (0009-L §2.11 ~ §2.15): the /api/v2 coordinates, the exact request
 * bodies (whole-body, TS cases, TS brief), the validated success and the structured read. No v1 path is ever used.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({ getRequest: (...a: unknown[]) => getRequest(...a), postRequest: (...a: unknown[]) => postRequest(...a) }))

import {
  cardDocumentPath,
  cardDocumentRead,
  cardDocumentRevise,
  cardDocumentRevisionPath,
  cardDocumentStructuredRead,
  cardDocumentConfirmRevision,
  toCardDocumentSnapshot,
  type CardDocumentContext,
} from '@/features/document'

const CTX: CardDocumentContext = { projectId: 'flowgate', flowId: 'flowgate.v02.0127', nodeKey: 'gn a/1', ownerSlot: 'instruction', openedDocId: 'flowgate.v02.0127.0030-TS' }
const BASE = '/api/v2/flows/flowgate.v02.0127/nodes/gn%20a%2F1/slots/instruction/document'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('paths and bodies', () => {
  it('every call goes to the card coordinates under /api/v2, escaped', () => {
    expect(cardDocumentPath(CTX)).toBe(BASE)
    expect(cardDocumentRevisionPath(CTX)).toBe(BASE + '/revisions')
    expect(cardDocumentRevisionPath(CTX, { kind: 'ts_spec', cases: [] })).toBe(BASE + '/test-spec/revisions')
    expect(cardDocumentRevisionPath(CTX, { kind: 'ts_brief', text: '' })).toBe(BASE + '/test-brief/revisions')
    expect(cardDocumentPath(CTX)).not.toContain('/api/v1')
  })

  it('reads the snapshot: an empty string stays a readable body, an unreadable body carries no text', async () => {
    getRequest.mockResolvedValueOnce({ data: { ok: true, doc_id: 'd', revision_no: 3, updated_at: 'u', body_state: 'readable', content: '', line: { slot: 'instruction' } } })
    await expect(cardDocumentRead(CTX)).resolves.toMatchObject({ docId: 'd', revisionNo: 3, content: '', bodyState: 'readable' })
    expect(getRequest).toHaveBeenCalledWith(BASE)
    expect(toCardDocumentSnapshot({ doc_id: 'd', revision_no: 1, body_state: 'read_error', content: 'ignored' }).content).toBeNull()
    expect(() => toCardDocumentSnapshot({ doc_id: 'd', revision_no: '1', body_state: 'readable', content: '' })).toThrow()
    expect(() => toCardDocumentSnapshot({ doc_id: 'd', revision_no: 1, body_state: 'readable', content: 5 })).toThrow()
  })

  it('whole-body revise sends exactly expected_doc_id / expected_revision / content / idempotency_key', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    await cardDocumentRevise(CTX, { expectedDocId: 'd', expectedRevision: 5, content: '본문', idempotencyKey: 'k-1' })
    expect(postRequest).toHaveBeenCalledWith(BASE + '/revisions', { expected_doc_id: 'd', expected_revision: 5, idempotency_key: 'k-1', content: '본문' })
  })

  it('TS cases send the cases array with title / intro / locale only when present; the brief sends text', async () => {
    postRequest.mockResolvedValue({ data: { ok: true } })
    await cardDocumentRevise(CTX, { expectedDocId: 'd', expectedRevision: 5, idempotencyKey: 'k-2', command: { kind: 'ts_spec', cases: [{ case_id: 'TC-1' }], title: 'Keep H1' } })
    expect(postRequest).toHaveBeenLastCalledWith(BASE + '/test-spec/revisions',
      { expected_doc_id: 'd', expected_revision: 5, idempotency_key: 'k-2', cases: [{ case_id: 'TC-1' }], title: 'Keep H1' })
    await cardDocumentRevise(CTX, { expectedDocId: 'd', expectedRevision: 6, idempotencyKey: 'k-3', command: { kind: 'ts_spec', cases: [] } })
    expect(postRequest).toHaveBeenLastCalledWith(BASE + '/test-spec/revisions', { expected_doc_id: 'd', expected_revision: 6, idempotency_key: 'k-3', cases: [] })
    await cardDocumentRevise(CTX, { expectedDocId: 'd', expectedRevision: 6, idempotencyKey: 'k-4', command: { kind: 'ts_brief', text: '지시' } })
    expect(postRequest).toHaveBeenLastCalledWith(BASE + '/test-brief/revisions', { expected_doc_id: 'd', expected_revision: 6, idempotency_key: 'k-4', text: '지시' })
  })

  it('the structured read is the same snapshot plus the server projection (null when unreadable)', async () => {
    getRequest.mockResolvedValueOnce({ data: { doc_id: 'd', revision_no: 2, body_state: 'readable', content: 'x', line: {}, projection: { text: 'brief' } } })
    await expect(cardDocumentStructuredRead(CTX, 'ts_brief')).resolves.toMatchObject({ revisionNo: 2, projection: { text: 'brief' } })
    expect(getRequest).toHaveBeenLastCalledWith(BASE + '/test-brief')
    getRequest.mockResolvedValueOnce({ data: { doc_id: 'd', revision_no: 2, body_state: 'file_not_found', content: null, line: {}, projection: null } })
    await expect(cardDocumentStructuredRead(CTX, 'ts_spec')).resolves.toMatchObject({ content: null, projection: null })
    expect(getRequest).toHaveBeenLastCalledWith(BASE + '/test-spec')
  })
})

describe('a success is judged by its fields, not by the HTTP status', () => {
  const ok = { ok: true, doc_id: 'd', revision_no: 6, updated_at: 'u', replayed: false, line: {} }
  it('accepts exactly expected+1 of the same document with updated_at, replayed and line', () => {
    expect(cardDocumentConfirmRevision(ok, 'd', 5)).toEqual({ docId: 'd', revisionNo: 6, updatedAt: 'u', replayed: false, line: {} })
    expect(cardDocumentConfirmRevision({ ...ok, replayed: true }, 'd', 5)?.replayed).toBe(true)
  })
  it.each([
    ['not ok', { ...ok, ok: false }], ['another document', { ...ok, doc_id: 'e' }], ['revision +2', { ...ok, revision_no: 7 }],
    ['revision as a string', { ...ok, revision_no: '6' }], ['no updated_at', { ...ok, updated_at: '' }],
    ['replayed not a boolean', { ...ok, replayed: 1 }], ['no line', { ...ok, line: undefined }], ['empty body', {}], ['not an object', 'ok'],
  ])('refuses %s', (_name, raw) => {
    expect(cardDocumentConfirmRevision(raw, 'd', 5)).toBeNull()
  })
})
