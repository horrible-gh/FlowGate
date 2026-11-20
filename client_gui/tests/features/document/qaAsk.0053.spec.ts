/**
 * flowgate.v02.0053 T#6 — the two v0.1 Q&A endpoints the Inspector now wraps so a person can ask and an AI
 * answers (0053 CH §2): the request each sends (path, body), the snake_case answer -> camelCase, and how the
 * ai-request admission refusals (the ai-invoke envelope with a flat `code`) are read next to the q routes'
 * own refusals.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
}))

import { qaAsk, qaErrorOf, qaRequestAiAnswer, toQaAiRequestResult, toQaAskResult } from '@/features/document'

const DOC = 'flowgate.v02.0060.0009-T'

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
/** The ai-request admission refusal as the route answers it (ai-invoke envelope beside the q fields). */
const admission = (status: number, code: string) =>
  axiosError(status, { ok: false, http_status: status, error_message: 'm', help_url: 'h', doc_id: DOC, item_id: 31, code, message: 'm' })

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('qaAsk — POST /q/{doc_id}/questions (flowgate.v02.0053 T#6)', () => {
  it('registers one question as a person and returns the added ids', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, doc_id: DOC, added_item_ids: [31] } })
    await expect(qaAsk({ docId: DOC, body: '완료 기준은 무엇인가요?' })).resolves.toEqual({ docId: DOC, itemIds: [31] })
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/q/${encodeURIComponent(DOC)}/questions`, {
      asker_kind: 'human',
      questions: [{ body: '완료 기준은 무엇인가요?' }],
    })
  })

  it('reads only integer ids', () => {
    expect(toQaAskResult({ ok: true, doc_id: DOC, added_item_ids: [3, 'x', 4.5, null, 5] })).toEqual({ docId: DOC, itemIds: [3, 5] })
    expect(toQaAskResult(null)).toEqual({ docId: null, itemIds: [] })
  })

  it('a refusal is thrown as it is', async () => {
    const error = axiosError(409, { ok: false, http_status: 409, error_message: 'Modification not allowed: the group has been disposed.' })
    postRequest.mockRejectedValue(error)
    await expect(qaAsk({ docId: DOC, body: 'q' })).rejects.toBe(error)
  })
})

describe('qaRequestAiAnswer — POST /q/{doc_id}/items/{id}/answers/ai-request (flowgate.v02.0053 T#6)', () => {
  it('asks an AI to answer the question with an empty body (the project provider chain)', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, doc_id: DOC, item_id: 31, run_id: 'aiv_9', status: 'running', provider: 'claude' } })
    await expect(qaRequestAiAnswer(DOC, 31)).resolves.toEqual({ docId: DOC, itemId: 31, runId: 'aiv_9', status: 'running' })
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/q/${encodeURIComponent(DOC)}/items/31/answers/ai-request`, {})
  })

  it('normalizes a thin answer', () => {
    expect(toQaAiRequestResult({ ok: true })).toEqual({ docId: null, itemId: null, runId: null, status: null })
  })
})

describe('qaErrorOf — the ai-request admission refusals (flowgate.v02.0053 T#6)', () => {
  it.each([
    ['409 run_in_progress', admission(409, 'run_in_progress'), 'running', 409, 'run_in_progress'],
    ['409 run_still_live', admission(409, 'run_still_live'), 'running', 409, 'run_still_live'],
    ['409 no_enabled_provider', admission(409, 'no_enabled_provider'), 'no_provider', 409, 'no_enabled_provider'],
    ['409 no_provider_registered', admission(409, 'no_provider_registered'), 'no_provider', 409, 'no_provider_registered'],
    ['409 provider_unavailable', admission(409, 'provider_unavailable'), 'no_provider', 409, 'provider_unavailable'],
    ['409 another admission code', admission(409, 'worktree_unavailable'), 'other', 409, 'worktree_unavailable'],
    ['409 disposed (q route, no code)', axiosError(409, { ok: false, http_status: 409, error_message: 'Modification not allowed: the group has been disposed.' }), 'disposed', 409, null],
    ['404 question gone', axiosError(404, { ok: false, http_status: 404, error_message: 'question_item 9 does not belong' }), 'not_found', 404, null],
    ['403', axiosError(403, { ok: false, http_status: 403, error_message: 'perm_document_create required' }), 'forbidden', 403, null],
    ['500 ai_dispatch_failed', admission(500, 'ai_dispatch_failed'), 'other', 500, 'ai_dispatch_failed'],
    ['423 mutation policy', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm' } }), 'running', 423, 'GROUP_AI_RUN_LOCKED'],
  ])('%s -> %s', (_label, error, kind, status, code) => {
    expect(qaErrorOf(error)).toEqual({ kind, status, code })
  })
})
