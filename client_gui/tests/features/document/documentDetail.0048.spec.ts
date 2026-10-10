/**
 * flowgate.v02.0048 T#5 — the documents tab's reads in features/document/api/document.ts (D#1 §6.3,
 * §12.1): the v0.1 `GET /api/v1/documents/{doc_id}` response (snake_case, row + rejection history +
 * AI review extras) -> DocumentDetail, and the superseded documents read one by one, a failed one
 * answered as missing without failing the others.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
vi.mock('@shared/api', () => ({ getRequest: (...args: unknown[]) => getRequest(...args) }))

import { documentDetailRead, documentErrorOf, documentRelationsRead, toDocumentDetail } from '@/features/document'

/** A rejected v0.1 TR as the server sends it (documents row + _parse_doc_workflow + review extras). */
const RESPONSE = {
  id: 912,
  doc_id: 'flowgate.v02.0048.0011-TR',
  project_id: 'flowgate',
  module: 'v02',
  group_id: 'flowgate.v02.0048',
  type_code: 'TR',
  seq: 11,
  title: '작업레포트 — T#1',
  status: 'open',
  target_id: 'flowgate.v02.0048.0010-T',
  triggered_by: 'flowgate.v02.0048.0001-R',
  superseded_by: null,
  doc_review_status: 'rejected',
  revision_no: 2,
  rejection_reason: '구조 검사 누락',
  rejection_history: [
    {
      rejection_id: 'rj_01',
      reason: '테스트 결과 누락',
      rejected_at: '2026-10-09T14:10:00+09:00',
      rejected_by: 'u1',
      ai_response: '테스트 결과를 추가했습니다.',
      responded_at: '2026-10-09T14:30:00+09:00',
      response_recorded_by: 'ai',
      response_revision_no: 1,
      rejection_provider: null,
      response_provider: null,
    },
    {
      rejection_id: 'rj_02',
      reason: '구조 검사 누락',
      rejected_at: '2026-10-09T15:00:00+09:00',
      rejected_by: 'u1',
      ai_response: null,
      responded_at: null,
      response_recorded_by: null,
      response_revision_no: null,
      review_id: 77,
      rejection_provider: { ai_run_id: 'run_9' },
      response_provider: null,
    },
  ],
  workflow_steps: ['R', 'WP', 'T', 'TR'],
  updated_at: '2026-10-09T15:00:00+09:00',
  download_available: true,
  ai_review: { id: 77, verdict: 'reject' },
  ai_review_history: [{ id: 77, verdict: 'reject' }],
  test_run: null,
  test_run_history: [],
  group_test_run: { active: false, run_id: null, doc_id: null, status: null },
}

const axiosError = (status: number, code?: string) => ({ isAxiosError: true, response: { status, data: code ? { detail: { code } } : { detail: 'x' } } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })

beforeEach(() => {
  getRequest.mockReset()
})

describe('toDocumentDetail', () => {
  it('takes the header fields, the rejection history (as stored, oldest first) and AI review existence', () => {
    expect(toDocumentDetail(RESPONSE)).toEqual({
      docId: 'flowgate.v02.0048.0011-TR',
      projectId: 'flowgate',
      groupId: 'flowgate.v02.0048',
      typeCode: 'TR',
      seq: 11,
      title: '작업레포트 — T#1',
      status: 'open',
      docReviewStatus: 'rejected',
      revisionNo: 2,
      rejectionReason: '구조 검사 누락',
      supersededBy: null,
      updatedAt: '2026-10-09T15:00:00+09:00',
      rejectionHistory: [
        { rejectionId: 'rj_01', reason: '테스트 결과 누락', rejectedAt: '2026-10-09T14:10:00+09:00', rejectedBy: 'u1', aiResponse: '테스트 결과를 추가했습니다.' },
        { rejectionId: 'rj_02', reason: '구조 검사 누락', rejectedAt: '2026-10-09T15:00:00+09:00', rejectedBy: 'u1', aiResponse: null },
      ],
      hasAiReview: true,
    })
  })

  it.each([
    ['no review at all', { ai_review: null, ai_review_history: [] }, false],
    ['history only', { ai_review: null, ai_review_history: [{ id: 1 }] }, true],
    ['latest only', { ai_review: { id: 1 } }, true],
    ['fields missing', {}, false],
    ['wrongly typed', { ai_review: 'yes', ai_review_history: 'many' }, false],
  ])('AI review existence: %s', (_label, extras, expected) => {
    expect(toDocumentDetail({ doc_id: 'd', ...extras }).hasAiReview).toBe(expected)
  })

  it('a history that is missing or not a list is empty; an item without a reason (or not an object) is dropped', () => {
    expect(toDocumentDetail({ doc_id: 'd' }).rejectionHistory).toEqual([])
    expect(toDocumentDetail({ doc_id: 'd', rejection_history: '[{"reason":"x"}]' }).rejectionHistory).toEqual([])
    expect(
      toDocumentDetail({ doc_id: 'd', rejection_history: [null, 'x', { reason: '' }, { reason: 3 }, { reason: '사유', rejected_at: 5 }] }).rejectionHistory,
    ).toEqual([{ rejectionId: null, reason: '사유', rejectedAt: null, rejectedBy: null, aiResponse: null }])
  })

  it('a response without doc_id is refused', () => {
    expect(() => toDocumentDetail({ title: 'x' })).toThrow('document response without doc_id')
    expect(() => toDocumentDetail(null)).toThrow()
  })
})

describe('documentDetailRead', () => {
  it('reads GET /api/v1/documents/{doc_id} (encoded) and normalizes the answer', async () => {
    getRequest.mockResolvedValueOnce({ data: RESPONSE })
    const detail = await documentDetailRead('flowgate.v02.0048.0011-TR')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/flowgate.v02.0048.0011-TR')
    expect(detail.rejectionHistory).toHaveLength(2)

    getRequest.mockResolvedValueOnce({ data: { doc_id: 'a/b c' } })
    await documentDetailRead('a/b c')
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents/a%2Fb%20c')
  })

  it.each([
    [axiosError(404), 'not_found', 404],
    [axiosError(403, 'permission_denied'), 'forbidden', 403],
    [axiosError(409, 'GROUP_DISPOSED'), 'conflict', 409],
    [networkError(), 'network', null],
  ])('a failed read is thrown as it is (%#)', async (error, kind, status) => {
    getRequest.mockRejectedValueOnce(error)
    const thrown = await documentDetailRead('d').catch((failure: unknown) => failure)
    expect(thrown).toBe(error)
    expect(documentErrorOf(thrown)).toMatchObject({ kind, status })
  })
})

describe('documentRelationsRead', () => {
  it('reads each id once, in the given order, and answers a failed read as missing with its error', async () => {
    const gone = axiosError(404)
    getRequest.mockImplementation(async (path: string) => {
      if (path.endsWith('0007-TR')) throw gone
      const docId = decodeURIComponent(path.split('/').pop() ?? '')
      return { data: { doc_id: docId, type_code: 'TR', seq: Number(docId.slice(-6, -3)), title: `rev of ${docId}`, doc_review_status: 'rejected' } }
    })
    const relations = await documentRelationsRead(['flowgate.v02.0048.0005-TR', 'flowgate.v02.0048.0007-TR', 'flowgate.v02.0048.0005-TR', ''])
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(relations.map((relation) => [relation.docId, relation.record?.title ?? null, relation.error])).toEqual([
      ['flowgate.v02.0048.0005-TR', 'rev of flowgate.v02.0048.0005-TR', null],
      ['flowgate.v02.0048.0007-TR', null, gone],
    ])
    expect(relations[0].record).toMatchObject({ typeCode: 'TR', seq: 5, docReviewStatus: 'rejected' })
  })

  it('reads nothing for no ids, and never throws even when every read fails', async () => {
    expect(await documentRelationsRead([])).toEqual([])
    expect(getRequest).not.toHaveBeenCalled()
    getRequest.mockRejectedValue(networkError())
    const relations = await documentRelationsRead(['a', 'b'])
    expect(relations.map((relation) => [relation.docId, relation.record, documentErrorOf(relation.error).kind])).toEqual([
      ['a', null, 'network'],
      ['b', null, 'network'],
    ])
  })
})
