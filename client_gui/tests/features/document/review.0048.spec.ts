/**
 * flowgate.v02.0048 T#6 — the header actions' calls in features/document/api/review.ts (D#1 §6.3,
 * §12.1): the request each one sends to the existing v0.1 endpoint (path, body, query), the
 * snake_case answers -> camelCase (review_transitions with and without tr_commit, ai-invoke start /
 * active, the document's group_disposed), and how a refusal is read for each server shape — the
 * mutation policy envelope (423 GROUP_AI_RUN_LOCKED), a review_transitions detail string (409
 * "group_disposed: …", other 409s), ai-invoke's flat code (409 run_in_progress), 403 / 404 / 422
 * and no answer. Errors are thrown as they are.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
}))

import {
  reviewActivityRead,
  reviewApprove,
  reviewDocumentRead,
  reviewErrorOf,
  reviewGroupParts,
  reviewReject,
  reviewRunStart,
  toReviewActivity,
  toReviewTransitionResult,
} from '@/features/document'

const DOC = 'flowgate.v02.0048.0011-TR'

/** The documents row as review_transitions returns it under `document` (trimmed). */
const ROW = {
  id: 912,
  doc_id: DOC,
  project_id: 'flowgate',
  group_id: 'flowgate.v02.0048',
  type_code: 'TR',
  seq: 11,
  title: '작업레포트 — T#1',
  status: 'open',
  doc_review_status: 'approved',
  revision_no: 2,
  rejection_reason: null,
  superseded_by: null,
  updated_at: '2026-10-09T18:00:00+09:00',
}

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('review_transitions approve / reject (flowgate.v02.0048 T#6)', () => {
  it('approve posts to the document path with an empty body and reads the document and the TR commit', async () => {
    postRequest.mockResolvedValue({
      data: { document: ROW, tr_commit: { committed: true, commit: '0123456789abcdef', subject: 'feat(x): y', skipped_reason: null, artifacts: [] } },
    })
    const result = await reviewApprove(DOC)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/flowgate.v02.0048.0011-TR/review_transitions/approve', {})
    expect(result.document).toMatchObject({ docId: DOC, docReviewStatus: 'approved', typeCode: 'TR', revisionNo: 2 })
    expect(result.trCommit).toEqual({ committed: true, commit: '0123456789abcdef', subject: 'feat(x): y', skippedReason: null })
  })

  it('a non-TR approval has no tr_commit key', async () => {
    postRequest.mockResolvedValue({ data: { document: { ...ROW, type_code: 'D' } } })
    expect((await reviewApprove(DOC)).trCommit).toBeNull()
  })

  it('a skipped TR commit keeps the server reason', () => {
    const result = toReviewTransitionResult({ document: ROW, tr_commit: { committed: false, commit: null, subject: null, skipped_reason: 'git_inactive' } })
    expect(result.trCommit).toEqual({ committed: false, commit: null, subject: null, skippedReason: 'git_inactive' })
  })

  it('reject posts the reason as comment', async () => {
    postRequest.mockResolvedValue({ data: { document: { ...ROW, doc_review_status: 'rejected', rejection_reason: '근거 부족' } } })
    const result = await reviewReject(DOC, '근거 부족')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/flowgate.v02.0048.0011-TR/review_transitions/reject', { comment: '근거 부족' })
    expect(result.document).toMatchObject({ docReviewStatus: 'rejected', rejectionReason: '근거 부족' })
  })

  it('encodes the doc_id in the path', async () => {
    postRequest.mockResolvedValue({ data: { document: { ...ROW, doc_id: 'p.m.0001-R' } } })
    await reviewApprove('p.m.0001-R')
    expect(postRequest.mock.calls[0][0]).toBe('/api/v1/documents/p.m.0001-R/review_transitions/approve')
    postRequest.mockResolvedValue({ data: { document: ROW } })
    await reviewApprove('a b/c')
    expect(postRequest.mock.calls[1][0]).toBe('/api/v1/documents/a%20b%2Fc/review_transitions/approve')
  })

  it('throws the transport error as it is', async () => {
    const error = axiosError(409, { detail: 'Invalid review transition' })
    postRequest.mockRejectedValue(error)
    await expect(reviewReject(DOC, 'x')).rejects.toBe(error)
  })

  it('an answer without a document is an error', () => {
    expect(() => toReviewTransitionResult({ ok: true })).toThrow()
  })
})

describe('ai-invoke start / active (flowgate.v02.0048 T#6)', () => {
  it('splits the Flow id into project / module / group', () => {
    expect(reviewGroupParts('flowgate.v02.0048')).toEqual({ project: 'flowgate', module: 'v02', group: '0048' })
    expect(reviewGroupParts('test2.default.0042')).toEqual({ project: 'test2', module: 'default', group: '0042' })
    expect(reviewGroupParts('flowgate')).toBeNull()
    expect(reviewGroupParts('a..b')).toBeNull()
  })

  it.each(['edit', 'rework'] as const)('run start (%s) sends one single run on the document', async (scope) => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'run_42', status: 'running', group_id: 'flowgate.v02.0048', doc_ref: DOC } })
    const run = await reviewRunStart({ groupId: 'flowgate.v02.0048', docId: DOC, scope })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/start', {
      project: 'flowgate',
      module: 'v02',
      group: '0048',
      doc_ref: DOC,
      action_scope: scope,
      mode: 'single',
    })
    expect(run).toEqual({ runId: 'run_42', status: 'running' })
  })

  it('run start refuses a Flow id that is not a group id, without a request', async () => {
    await expect(reviewRunStart({ groupId: 'flowgate', docId: DOC, scope: 'edit' })).rejects.toThrow()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('active reads the group run', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, run_id: 'run_42', status: 'running', active: true, group_id: 'flowgate.v02.0048', doc_ref: DOC } })
    const activity = await reviewActivityRead('flowgate.v02.0048')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: 'flowgate.v02.0048' })
    expect(activity).toEqual({ active: true, runId: 'run_42', status: 'running', docRef: DOC, actionScope: null })
  })

  it('no run, or a finished one, is not active', () => {
    expect(toReviewActivity({ ok: true, active: false, group_id: 'g' })).toEqual({ active: false, runId: null, status: null, docRef: null, actionScope: null })
    expect(toReviewActivity({ active: true, run_id: 'r', status: 'finished' }).active).toBe(false)
    expect(toReviewActivity(null).active).toBe(false)
  })
})

describe('document review state (flowgate.v02.0048 T#6)', () => {
  it('reads the document and its group_disposed flag', async () => {
    getRequest.mockResolvedValue({ data: { ...ROW, doc_review_status: 'pending_review', group_disposed: true, is_editable: false } })
    const read = await reviewDocumentRead(DOC)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/flowgate.v02.0048.0011-TR')
    expect(read.record).toMatchObject({ docId: DOC, docReviewStatus: 'pending_review' })
    expect(read.groupDisposed).toBe(true)
  })

  it('a live group (or no flag) is not disposed', async () => {
    getRequest.mockResolvedValue({ data: { ...ROW, group_disposed: false } })
    expect((await reviewDocumentRead(DOC)).groupDisposed).toBe(false)
    getRequest.mockResolvedValue({ data: ROW })
    expect((await reviewDocumentRead(DOC)).groupDisposed).toBe(false)
  })
})

describe('reviewErrorOf (flowgate.v02.0048 T#6)', () => {
  it.each([
    [
      '423 mutation policy envelope (AI run owns the group)',
      axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'x', run_live: true } }),
      { kind: 'running', status: 423, code: 'GROUP_AI_RUN_LOCKED' },
    ],
    ['423 without a code (locked group)', axiosError(423, { detail: 'locked' }), { kind: 'running', status: 423, code: null }],
    ['409 ai-invoke flat run_in_progress', axiosError(409, { code: 'run_in_progress', message: 'x' }), { kind: 'running', status: 409, code: 'run_in_progress' }],
    ['409 group_disposed detail string', axiosError(409, { detail: 'group_disposed: d belongs to a disposed group' }), { kind: 'disposed', status: 409, code: null }],
    ['409 transition detail string', axiosError(409, { detail: "Invalid review transition: doc_review_status='approved' + action='approve'" }), { kind: 'conflict', status: 409, code: null }],
    ['409 ai-invoke flat other code', axiosError(409, { code: 'no_enabled_provider', message: 'x' }), { kind: 'conflict', status: 409, code: 'no_enabled_provider' }],
    ['403', axiosError(403, { detail: 'Insufficient permissions' }), { kind: 'forbidden', status: 403, code: null }],
    ['403 ai-invoke permission_denied', axiosError(403, { code: 'permission_denied' }), { kind: 'forbidden', status: 403, code: 'permission_denied' }],
    ['404', axiosError(404, { detail: 'Document not found' }), { kind: 'not_found', status: 404, code: null }],
    ['422 validation_failed', axiosError(422, { code: 'validation_failed', errors: [] }), { kind: 'invalid', status: 422, code: 'validation_failed' }],
    ['400', axiosError(400, { detail: 'Comment required' }), { kind: 'invalid', status: 400, code: null }],
    ['500', axiosError(500, {}), { kind: 'other', status: 500, code: null }],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, { kind: 'network', status: null, code: null }],
    ['not a transport error', new Error('not a group id'), { kind: 'other', status: null, code: null }],
  ])('%s', (_label, error, expected) => {
    expect(reviewErrorOf(error)).toEqual(expected)
  })
})

it.each(['new', 'edit', 'rework', 'review', 'chat'])('retains activity action scope %s', (scope) => { expect(toReviewActivity({ active: true, status: 'running', doc_ref: DOC, action_scope: scope }).actionScope).toBe(scope) })
