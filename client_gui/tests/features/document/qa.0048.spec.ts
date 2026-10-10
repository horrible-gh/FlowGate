/**
 * flowgate.v02.0048 T#7 — the Q&A tab's calls in features/document/api/qa.ts (D#1 §6.3, §12.1): the
 * request each one sends to the existing v0.1 endpoint (path, body), the snake_case answer -> camelCase
 * (the Q&A tree as `GET /q/{doc_id}` returns it on the dev server: AI and human questions, options,
 * answers with picked options and provider snapshots, an empty container), and how a refusal is read
 * for each server shape — the mutation policy envelope (423 GROUP_AI_RUN_LOCKED), the q routes'
 * `{ ok: false, http_status, error_message }` (409 disposed, 403, 404, 400), 422 and no answer.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
}))

import { qaAnswer, qaErrorOf, qaRead, toQaAnswerResult, toQaThread } from '@/features/document'

const DOC = 'test2.default.0017.0002-N'

/** The tree as the dev server returned it (2026-10-09, trimmed bodies). */
const TREE = {
  ok: true,
  qa: {
    id: 6,
    q_id: DOC,
    project_id: 'test2',
    title: '조사지시 승인',
    created_by: 'u-system',
    status: 'pending',
    doc_id: DOC,
    items: [
      {
        id: 10,
        question_id: 6,
        seq: 1,
        body: '조사 대상과 완료 기준을 알려 주십시오.',
        answer_count: 0,
        title: '조사 대상 및 완료 기준 확인 요청',
        asker_kind: 'ai',
        options: [
          { id: 'o1', label: '코드 품질 점검' },
          { id: 'o2', label: '성능 점검' },
        ],
        asker_provider: { ai_run_id: 'aiv_1', ai_provider_id: 'p_claude', ai_provider_name: 'Claude Opus 5.5' },
        answers: [],
      },
      {
        id: 17,
        question_id: 6,
        seq: 3,
        body: '조사 범위를 가정했습니다.',
        answer_count: 1,
        title: '조사 범위 가정 안내',
        asker_kind: 'ai',
        options: [],
        asker_provider: null,
        answers: [
          {
            id: 9,
            question_item_id: 17,
            body: 'ok',
            author_kind: 'human',
            author_id: 'b584fdac',
            is_accepted: 0,
            created_at: '2026-09-23T00:02:25.819Z',
            selected_options: ['o9', 3],
            author_provider: null,
          },
          {
            id: 11,
            body: '보완했습니다.',
            author_kind: 'ai',
            author_id: null,
            selected_options: [],
            author_provider: { ai_run_id: 'aiv_2', ai_provider_id: null, ai_provider_name: null },
          },
        ],
      },
      { id: 'bad', seq: 4, body: 'no numeric id', asker_kind: 'human' },
      { id: 19, seq: 2, body: 'CLI E2E smoke question', title: null, asker_kind: 'human' },
    ],
  },
}

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('GET /q/{doc_id} (flowgate.v02.0048 T#7)', () => {
  it('reads the document path (encoded) and normalizes the tree', async () => {
    getRequest.mockResolvedValue({ data: TREE })
    const thread = await qaRead(DOC)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/q/${encodeURIComponent(DOC)}`)
    expect(thread.docId).toBe(DOC)
    expect(thread.status).toBe('pending')
    expect(thread.items.map((item) => item.id)).toEqual([10, 17, 19])
    expect(thread.items[0]).toEqual({
      id: 10,
      seq: 1,
      title: '조사 대상 및 완료 기준 확인 요청',
      body: '조사 대상과 완료 기준을 알려 주십시오.',
      askerKind: 'ai',
      options: [
        { id: 'o1', label: '코드 품질 점검' },
        { id: 'o2', label: '성능 점검' },
      ],
      answerCount: 0,
      answers: [],
      provider: { runId: 'aiv_1', providerId: 'p_claude', providerName: 'Claude Opus 5.5' },
      createdAt: null,
    })
  })

  it('answers keep their side, picked option ids (strings only) and provider snapshot', () => {
    const [, answered] = toQaThread(TREE).items
    expect(answered.answerCount).toBe(1)
    expect(answered.provider).toBeNull()
    expect(answered.answers).toEqual([
      { id: 9, body: 'ok', authorKind: 'human', authorId: 'b584fdac', selectedOptionIds: ['o9'], provider: null, createdAt: '2026-09-23T00:02:25.819Z' },
      { id: 11, body: '보완했습니다.', authorKind: 'ai', authorId: null, selectedOptionIds: [], provider: { runId: 'aiv_2', providerId: null, providerName: null }, createdAt: null },
    ])
  })

  it('missing fields become empty lists / zero / null', () => {
    const smoke = toQaThread(TREE).items[2]
    expect(smoke).toEqual({ id: 19, seq: 2, title: null, body: 'CLI E2E smoke question', askerKind: 'human', options: [], answerCount: 0, answers: [], provider: null, createdAt: null })
  })

  it('a document without a container is an empty thread', () => {
    expect(toQaThread({ ok: true, qa: { doc_id: DOC, status: null, items: [] } })).toEqual({ docId: DOC, status: null, items: [] })
  })

  it('a response without the document id is refused', () => {
    expect(() => toQaThread({ ok: true })).toThrow()
    expect(() => toQaThread({ ok: true, qa: { items: [] } })).toThrow()
  })

  it('a transport error is thrown as it is', async () => {
    const error = axiosError(403, { ok: false, http_status: 403, error_message: 'Insufficient permissions' })
    getRequest.mockRejectedValue(error)
    await expect(qaRead(DOC)).rejects.toBe(error)
  })
})

describe('POST /q/{doc_id}/items/{item_id}/answers (flowgate.v02.0048 T#7)', () => {
  it('posts a human answer with the prose and the picked option', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, doc_id: DOC, item_id: 10, answer_id: 21, author_kind: 'human', status: 'pending' } })
    const result = await qaAnswer({ docId: DOC, itemId: 10, body: '코드 품질 위주로', selectedOptionIds: ['o1'] })
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/q/${encodeURIComponent(DOC)}/items/10/answers`, {
      body: '코드 품질 위주로',
      author_kind: 'human',
      selected_option_ids: ['o1'],
    })
    expect(result).toEqual({ docId: DOC, itemId: 10, answerId: 21, authorKind: 'human', status: 'pending' })
  })

  it('a pick alone is sent with an empty body (the server fills the label)', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, doc_id: DOC, item_id: 10, answer_id: 22, author_kind: 'human', status: 'done' } })
    await qaAnswer({ docId: DOC, itemId: 10, body: '', selectedOptionIds: ['o2'] })
    expect(postRequest.mock.calls[0][1]).toEqual({ body: '', author_kind: 'human', selected_option_ids: ['o2'] })
  })

  it('normalizes a partial answer response', () => {
    expect(toQaAnswerResult({ ok: true })).toEqual({ docId: null, itemId: null, answerId: null, authorKind: null, status: null })
  })

  it('a refusal is thrown as it is', async () => {
    const error = axiosError(409, { ok: false, http_status: 409, error_message: 'Modification not allowed: the group has been disposed.' })
    postRequest.mockRejectedValue(error)
    await expect(qaAnswer({ docId: DOC, itemId: 10, body: 'x', selectedOptionIds: [] })).rejects.toBe(error)
  })
})

describe('qaErrorOf — refusal shapes (flowgate.v02.0048 T#7)', () => {
  it.each([
    ['423 mutation policy envelope', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: '…', run_live: true } }), 'running', 423, 'GROUP_AI_RUN_LOCKED'],
    ['423 without code', axiosError(423, {}), 'running', 423, null],
    ['409 disposed (q route)', axiosError(409, { ok: false, http_status: 409, error_message: 'Modification not allowed: the group has been disposed.' }), 'disposed', 409, null],
    ['403 permission', axiosError(403, { ok: false, http_status: 403, error_message: 'Insufficient permissions' }), 'forbidden', 403, null],
    ['404 question gone', axiosError(404, { ok: false, http_status: 404, error_message: 'question_item 9 does not exist' }), 'not_found', 404, null],
    ['400 unknown option', axiosError(400, { ok: false, http_status: 400, error_message: 'unknown option id: o9' }), 'invalid', 400, null],
    ['422 validation', axiosError(422, { detail: [{ loc: ['body'], msg: 'body must not be empty' }] }), 'invalid', 422, null],
    ['500', axiosError(500, 'Internal Server Error'), 'other', 500, null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null],
  ])('%s -> %s', (_label, error, kind, status, code) => {
    expect(qaErrorOf(error)).toEqual({ kind, status, code })
  })

  it('reads the status from the body when the transport has none', () => {
    expect(qaErrorOf({ response: { data: { ok: false, http_status: 403 } } }).kind).toBe('forbidden')
  })
})
