/**
 * flowgate.v02.0048 T#3 — the single document read of features/document (D#1 §6.3) and the common
 * error reading (D#1 §6.2): the v0.1 `GET /api/v1/documents/{doc_id}` response (snake_case, row +
 * workflow / review extras) -> DocumentRecord, the request path, errors passed through as thrown,
 * and status / code / kind of a failed call (never the server message).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
vi.mock('@shared/api', () => ({ getRequest: (...args: unknown[]) => getRequest(...args) }))

import { documentErrorOf, documentRead, toDocumentRecord } from '@/features/document'

/** A v0.1 document response as the server sends it (documents row + _parse_doc_workflow extras). */
const RESPONSE = {
  id: 912,
  doc_id: 'flowgate.v02.0048.0011-TR',
  project_id: 'flowgate',
  module: 'v02',
  group_id: 'flowgate.v02.0048',
  sub_group_id: null,
  type_code: 'TR',
  seq: 11,
  title: '작업레포트 — T#1',
  file_path: 'documents/flowgate/main/v02/0048/0011-TR_document.md',
  status: 'open',
  owner_id: '4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39',
  target_id: 'flowgate.v02.0048.0010-T',
  triggered_by: 'flowgate.v02.0048.0001-R',
  superseded_by: null,
  doc_review_status: 'rejected',
  revision_no: 1,
  rejection_reason: '구조 검사 누락',
  rejection_history: [{ reason: '구조 검사 누락', rejected_at: '2026-10-09T15:00:00+09:00' }],
  workflow_steps: ['R', 'WP', 'T', 'TR'],
  created_at: '2026-10-09T14:00:00+09:00',
  updated_at: '2026-10-09T15:00:00+09:00',
  download_available: true,
  ai_review: null,
  ai_review_history: [],
  test_run: null,
  test_run_history: [],
  group_test_run: null,
}

beforeEach(() => {
  getRequest.mockReset()
})

describe('toDocumentRecord', () => {
  it('takes the header fields of the real response, camelCase', () => {
    expect(toDocumentRecord(RESPONSE)).toEqual({
      docId: 'flowgate.v02.0048.0011-TR',
      projectId: 'flowgate',
      groupId: 'flowgate.v02.0048',
      typeCode: 'TR',
      seq: 11,
      title: '작업레포트 — T#1',
      status: 'open',
      docReviewStatus: 'rejected',
      revisionNo: 1,
      rejectionReason: '구조 검사 누락',
      supersededBy: null,
      updatedAt: '2026-10-09T15:00:00+09:00',
    })
  })

  it('missing, empty or wrongly typed fields are null (an unknown status stays as sent)', () => {
    expect(
      toDocumentRecord({ doc_id: 'd', title: 7, seq: '3', revision_no: 1.5, status: '', doc_review_status: 'wf_done', group_id: null }),
    ).toEqual({
      docId: 'd',
      projectId: null,
      groupId: null,
      typeCode: null,
      seq: null,
      title: '',
      status: null,
      docReviewStatus: 'wf_done',
      revisionNo: null,
      rejectionReason: null,
      supersededBy: null,
      updatedAt: null,
    })
  })

  it('a response without doc_id is refused', () => {
    expect(() => toDocumentRecord({ title: 'x' })).toThrow()
    expect(() => toDocumentRecord(null)).toThrow()
    expect(() => toDocumentRecord('<html>')).toThrow()
  })
})

describe('documentRead', () => {
  it('GETs the v0.1 document path (doc id encoded) and normalizes the answer', async () => {
    getRequest.mockResolvedValue({ data: RESPONSE })
    await expect(documentRead('flowgate.v02.0048.0011-TR')).resolves.toMatchObject({ docId: 'flowgate.v02.0048.0011-TR', docReviewStatus: 'rejected' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/flowgate.v02.0048.0011-TR')

    getRequest.mockResolvedValue({ data: { ...RESPONSE, doc_id: 'a b/c' } })
    await documentRead('a b/c')
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents/a%20b%2Fc')
  })

  it('throws the transport error as it is', async () => {
    const notFound = { isAxiosError: true, response: { status: 404, data: { detail: 'Document not found: x' } } }
    getRequest.mockRejectedValue(notFound)
    await expect(documentRead('x')).rejects.toBe(notFound)
  })
})

describe('documentErrorOf', () => {
  const http = (status: number, data: unknown = {}) => ({ isAxiosError: true, response: { status, data } })

  it.each([
    [http(409, { detail: { code: 'DOCUMENT_NOT_MUTABLE', message: 'AI 실행 중' } }), { kind: 'conflict', status: 409, code: 'DOCUMENT_NOT_MUTABLE' }],
    [http(409, { error: { code: 'GROUP_AI_RUN_LOCKED' } }), { kind: 'conflict', status: 409, code: 'GROUP_AI_RUN_LOCKED' }],
    [http(403, { detail: 'Permission denied' }), { kind: 'forbidden', status: 403, code: null }],
    [http(404, { detail: 'Document not found: x' }), { kind: 'not_found', status: 404, code: null }],
    [http(500), { kind: 'other', status: 500, code: null }],
    [{ isAxiosError: true, request: {} }, { kind: 'network', status: null, code: null }],
    [new Error('document response without doc_id'), { kind: 'other', status: null, code: null }],
  ])('%#: %j', (error, expected) => {
    expect(documentErrorOf(error)).toEqual(expected)
  })
})
