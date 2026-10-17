/**
 * flowgate.v02.0056 F2-2 — the transport of the O3 contracts (flowgate.v02.0056.0009-TR §4), with `@shared/api` mocked:
 * - §4-1 card line fields: `has_body` and the history counts on the Canvas card line, kept only when sent;
 * - §4-2 [작성]: POST …/slots/{slot}/document { content }, the 201 answer (bound_as_result, line) and a refusal's
 *   code · message · first 422 error · doc_id;
 * - §4-3 the single AI call: POST /ai-invoke/start with action_scope edit · mode single · provider_pinned · messages
 *   and no document_review_loop; the run and the group's live run; the v0.1 flat refusal (409 run_in_progress);
 * - §4-5 card memos: list · create · change (the fields given only) · delete, and a refusal's 422 field / reason.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
  deleteRequest: vi.fn(),
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

const {
  cardLineErrorOf,
  cardLineWrite,
  cardMemoCreate,
  cardMemoDelete,
  cardMemoErrorOf,
  cardMemoList,
  cardMemoPayload,
  cardMemoUpdate,
  documentAiActiveRead,
  documentAiErrorOf,
  documentAiRunRead,
  documentAiStart,
  documentAiStartPayload,
} = await import('@/features/document')
const { toCardDocuments } = await import('@/features/project/flowCanvasApi')

const FLOW = 'flowgate.v02.0056'
const answer = (data: unknown) => ({ data })
const refusal = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('§4-1 card line fields on the Canvas read', () => {
  it('has_body and the three counts are kept when sent', () => {
    expect(
      toCardDocuments([
        { slot: 'report', doc_type: 'TR', author: 'run', doc_id: 'd1', title: 't', status: 'draft', doc_review_status: null, has_body: false, review_count: 2, rejection_count: 1, history_count: 3 },
      ]),
    ).toEqual([
      { slot: 'report', docType: 'TR', author: 'run', docId: 'd1', title: 't', status: 'draft', docReviewStatus: null, hasBody: false, reviewCount: 2, rejectionCount: 1, historyCount: 3 },
    ])
  })

  it('a line without them (an older server) carries none; a broken count is dropped', () => {
    const [line] = toCardDocuments([{ slot: 'instruction', doc_id: null, review_count: -1, rejection_count: 1.5, history_count: '3', has_body: 'yes' }])
    expect(line).toEqual({ slot: 'instruction', docType: null, author: null, docId: null, title: null, status: null, docReviewStatus: null })
  })
})

describe('§4-2 [작성] of a card line', () => {
  it('posts the content to the slot path and reads the 201 answer', async () => {
    api.postRequest.mockResolvedValue(
      answer({
        ok: true,
        flow_id: FLOW,
        node_key: 'gn_t',
        slot: 'report',
        doc_type: 'TR',
        doc_id: `${FLOW}.0021-TR`,
        bound_as_result: true,
        line: { slot: 'report', doc_type: 'TR', author: 'run', doc_id: `${FLOW}.0021-TR`, title: 'TR', status: 'open', doc_review_status: 'pending_review', has_body: true, review_count: 0, rejection_count: 0, history_count: 0 },
      }),
    )
    const result = await cardLineWrite(FLOW, 'gn_t', 'report', '# 결과')
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/slots/report/document`, { content: '# 결과' })
    expect(result).toEqual({
      flowId: FLOW,
      nodeKey: 'gn_t',
      slot: 'report',
      docType: 'TR',
      docId: `${FLOW}.0021-TR`,
      boundAsResult: true,
      line: {
        slot: 'report',
        docType: 'TR',
        author: 'run',
        docId: `${FLOW}.0021-TR`,
        title: 'TR',
        status: 'open',
        docReviewStatus: 'pending_review',
        hasBody: true,
        reviewCount: 0,
        rejectionCount: 0,
        historyCount: 0,
      },
    })
  })

  it('null content is sent as null (an empty draft); a general write is not bound', async () => {
    api.postRequest.mockResolvedValue(answer({ ok: true, doc_id: 'd', bound_as_result: false, line: null }))
    const result = await cardLineWrite(FLOW, 'gn_wp', 'plan', null)
    expect(api.postRequest.mock.calls[0][1]).toEqual({ content: null })
    expect(result).toMatchObject({ flowId: FLOW, nodeKey: 'gn_wp', slot: 'plan', boundAsResult: false, line: null })
  })

  it('a refusal: code, status, message, the first 422 error and the doc_id of slot_has_document', () => {
    expect(
      cardLineErrorOf(refusal(409, { ok: false, error: { code: 'slot_has_document', message: '이미 문서가 있습니다', params: { flow_id: FLOW, doc_id: 'd9' } } })),
    ).toEqual({ code: 'slot_has_document', status: 409, network: false, message: '이미 문서가 있습니다', field: null, reason: null, docId: 'd9' })
    expect(
      cardLineErrorOf(refusal(422, { ok: false, error: { code: 'invalid_request', message: 'm', params: { errors: [{ field: 'content', reason: 'empty_body' }] } } })),
    ).toMatchObject({ code: 'invalid_request', status: 422, field: 'content', reason: 'empty_body', docId: null })
    expect(cardLineErrorOf({ isAxiosError: true, request: {} })).toMatchObject({ network: true, code: null })
  })
})

describe('§4-3 the single AI call', () => {
  it('the start body: edit · single · the pinned AI · the opinion; no document_review_loop', () => {
    const body = documentAiStartPayload({ groupId: FLOW, docId: `${FLOW}.0021-TR`, providerId: 'aip_opus', messages: ['  ', '이 부분을 고쳐 주세요'] })
    expect(body).toEqual({
      project: 'flowgate',
      module: 'v02',
      group: '0056',
      doc_ref: `${FLOW}.0021-TR`,
      action_scope: 'edit',
      mode: 'single',
      provider_id: 'aip_opus',
      provider_pinned: true,
      messages: ['이 부분을 고쳐 주세요'],
    })
    expect(body).not.toHaveProperty('document_review_loop')
    expect(() => documentAiStartPayload({ groupId: 'nogroup', docId: 'd', providerId: 'p', messages: [] })).toThrow()
  })

  it('starts the run, reads it and the group live run', async () => {
    api.postRequest.mockResolvedValue(answer({ run_id: 'run_1', status: 'running' }))
    await expect(documentAiStart({ groupId: FLOW, docId: 'd', providerId: 'p', messages: [] })).resolves.toEqual({ runId: 'run_1', status: 'running', endReason: null, finished: false })
    expect(api.postRequest.mock.calls[0][0]).toBe('/api/v1/ai-invoke/start')
    api.getRequest.mockResolvedValueOnce(answer({ status: 'finished', end_reason: 'completed' }))
    await expect(documentAiRunRead('run_1')).resolves.toEqual({ runId: 'run_1', status: 'finished', endReason: 'completed', finished: true })
    expect(api.getRequest).toHaveBeenLastCalledWith('/api/v1/ai-invoke/run_1')
    api.getRequest.mockResolvedValueOnce(answer({ active: true, run_id: 'run_2', status: 'running', doc_ref: 'd2' }))
    await expect(documentAiActiveRead(FLOW)).resolves.toEqual({ active: true, runId: 'run_2', status: 'running', docRef: 'd2' })
    expect(api.getRequest).toHaveBeenLastCalledWith('/api/v1/ai-invoke/active', { group_id: FLOW })
  })

  it('a start without a run id is a failure', async () => {
    api.postRequest.mockResolvedValue(answer({ status: 'running' }))
    await expect(documentAiStart({ groupId: FLOW, docId: 'd', providerId: 'p', messages: [] })).rejects.toThrow()
  })

  it('the v0.1 flat refusal: 409 run_in_progress with its message and the run holding the group', () => {
    expect(documentAiErrorOf(refusal(409, { code: 'run_in_progress', message: '다른 AI 실행이 진행 중입니다', run_id: 'run_9' }))).toEqual({
      code: 'run_in_progress',
      status: 409,
      network: false,
      message: '다른 AI 실행이 진행 중입니다',
      runId: 'run_9',
    })
    expect(documentAiErrorOf(refusal(409, { detail: { code: 'run_in_progress', message: 'm', run_id: 'r' } }))).toMatchObject({ code: 'run_in_progress', message: 'm', runId: 'r' })
  })
})

describe('§4-5 card memos', () => {
  const MEMO = { memo_id: 7, title: '배경', body: '내용', ai_ref: true, created_by: 'u1', created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T01:00:00Z', updated_by: 'u1' }
  const memo = { memoId: '7', title: '배경', body: '내용', aiRef: true, createdBy: 'u1', createdAt: '2026-10-13T00:00:00Z', updatedAt: '2026-10-13T01:00:00Z', updatedBy: 'u1' }

  it('lists a card memos in the written order', async () => {
    api.getRequest.mockResolvedValue(answer({ ok: true, flow_id: FLOW, node_key: 'gn_t', memos: [MEMO, { ...MEMO, memo_id: 'm8', ai_ref: false }, { title: 'no id' }] }))
    const memos = await cardMemoList(FLOW, 'gn_t')
    expect(api.getRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/memos`)
    expect(memos.map((item) => [item.memoId, item.aiRef])).toEqual([
      ['7', true],
      ['m8', false],
    ])
    expect(memos[0]).toEqual(memo)
  })

  it('creates, changes (the given fields only) and deletes', async () => {
    api.postRequest.mockResolvedValue(answer({ ok: true, memo: MEMO }))
    await expect(cardMemoCreate(FLOW, 'gn_t', { title: '배경', body: '내용', aiRef: false })).resolves.toEqual(memo)
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/memos`, { title: '배경', body: '내용', ai_ref: false })
    api.patchRequest.mockResolvedValue(answer({ ok: true, memo: { ...MEMO, ai_ref: false } }))
    await expect(cardMemoUpdate(FLOW, 'gn_t', '7', { aiRef: false })).resolves.toMatchObject({ memoId: '7', aiRef: false })
    expect(api.patchRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/memos/7`, { ai_ref: false })
    api.deleteRequest.mockResolvedValue(answer({ ok: true, memo_id: '7', deleted: true }))
    await cardMemoDelete(FLOW, 'gn_t', '7')
    expect(api.deleteRequest).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/nodes/gn_t/memos/7`)
    expect(cardMemoPayload({})).toEqual({})
  })

  it('an answer without a memo is a failure', async () => {
    api.postRequest.mockResolvedValue(answer({ ok: true }))
    await expect(cardMemoCreate(FLOW, 'gn_t', { title: 'x' })).rejects.toThrow()
  })

  it('a refusal: code, status, message and the first 422 error', () => {
    expect(
      cardMemoErrorOf(refusal(422, { ok: false, error: { code: 'invalid_request', message: '제목이 깁니다', params: { errors: [{ field: 'title', reason: 'too_long', max: 200 }] } } })),
    ).toEqual({ code: 'invalid_request', status: 422, network: false, message: '제목이 깁니다', field: 'title', reason: 'too_long' })
    expect(cardMemoErrorOf(refusal(404, { ok: false, error: { code: 'memo_not_found', message: '', params: { memo_id: '7' } } }))).toMatchObject({
      code: 'memo_not_found',
      status: 404,
      field: null,
    })
  })
})
