/**
 * flowgate.v02.0048 T#12 — the check card's calls in features/document/api/check.ts (D#1 §6.3, §12.1;
 * NR0003 §2.8): the requests sent to the existing v0.1 endpoints (GET /documents/{doc_id}, GET
 * /groups/{gid}/git/finalize?context=approval, POST /documents/review_transitions/approve with the
 * Git choice), the server's snake_case answers (finalize state, approval_publish.response_payload
 * shapes, the plain transition) -> camelCase, and how a refusal is read — FastAPI `detail`, the
 * `{ ok:false, error:{ code } }` envelope of the final approval, the mutation policy 423, no answer.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
  patchRequest: vi.fn(),
}))

import {
  CHECK_GIT_ACTIONS,
  CHECK_GIT_CHOICES,
  checkApprove,
  checkDocumentRead,
  checkErrorOf,
  checkGitRead,
  toCheckApproveResult,
  toCheckGitState,
  type CheckErrorKind,
} from '@/features/document'

const AC = 'test2.default.0034.0006-AC'
const GROUP = 'test2.default.0034'

const docRow = (status: string, fields: Record<string, unknown> = {}) => ({
  doc_id: AC,
  project_id: 'test2',
  group_id: GROUP,
  type_code: 'AC',
  seq: 6,
  title: 'Final Approval',
  status: 'draft',
  doc_review_status: status,
  ...fields,
})

/** get_finalize_state of a Git active group whose AC still waits (context=approval). */
const awaitingState = (fields: Record<string, unknown> = {}) => ({
  group_id: GROUP,
  branch: 'test2_default_0034',
  base_branch: 'main',
  work_base_ref: 'main',
  default_target: 'main',
  default_target_unmerge_supported: true,
  status: 'awaiting_choice',
  default_action: 'merge',
  choices: ['merge', 'merge_only', 'wait'],
  aux_choices: ['push'],
  action_axes: { scopes: ['merge', 'commit', 'none'], matrix: {}, commit_actions: [] },
  ahead_count: 2,
  behind_count: 0,
  base_remote_behind_count: 0,
  merge_id: null,
  merge_commit: null,
  review_state: null,
  approval_pending: false,
  approval_in_flight: false,
  approval_job: null,
  ...fields,
})

/** The _NONE_STATE answer (not Git active, nothing to reflect). */
const noneState = { group_id: GROUP, branch: null, base_branch: null, status: 'none', default_action: null, choices: [], aux_choices: [], action_axes: null, ahead_count: null, behind_count: null, work_base_ref: null, merge_id: null, base_remote_behind_count: null, approval_in_flight: false, approval_job: null }

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('check API — requests (flowgate.v02.0048 T#12)', () => {
  it('checkDocumentRead reads GET /api/v1/documents/{doc_id} and normalizes it the review way', async () => {
    getRequest.mockResolvedValue({ data: docRow('pending_review', { group_disposed: true }) })
    const read = await checkDocumentRead(AC)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(AC)}`)
    expect(read.record.docId).toBe(AC)
    expect(read.record.docReviewStatus).toBe('pending_review')
    expect(read.groupDisposed).toBe(true)
  })

  it('checkGitRead reads GET /api/v1/groups/{gid}/git/finalize with context=approval', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, state: awaitingState() } })
    const state = await checkGitRead(GROUP)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/groups/${GROUP}/git/finalize`, { context: 'approval' })
    expect(state.status).toBe('awaiting_choice')
    expect(state.choices).toEqual(['merge', 'merge_only', 'wait'])
  })

  it.each([
    ['merge', { doc_id: AC, comment: null, git_action: 'merge' }],
    ['merge_only', { doc_id: AC, comment: null, git_action: 'merge_only' }],
    ['wait', { doc_id: AC, comment: null }],
    [null, { doc_id: AC, comment: null }],
  ] as const)('checkApprove(%s) posts the body-form approve with git_action only for a Git-changing choice', async (choice, body) => {
    postRequest.mockResolvedValue({ data: { document: docRow('approved') } })
    await checkApprove({ docId: AC, choice })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/review_transitions/approve', body)
    // Never a merge target: the server's default (the group's work base) applies.
    expect(Object.keys(postRequest.mock.calls[0][1])).not.toContain('git_target_branch')
  })

  it('the choices and the actions sent are the server main choices only', () => {
    expect(CHECK_GIT_CHOICES).toEqual(['merge', 'merge_only', 'wait'])
    expect(CHECK_GIT_ACTIONS).toEqual(['merge', 'merge_only'])
  })

  it('transport errors are thrown as they are', async () => {
    const refusal = axiosError(403, { ok: false, error: { code: 'forbidden', message: 'Forbidden' } })
    getRequest.mockRejectedValue(refusal)
    await expect(checkGitRead(GROUP)).rejects.toBe(refusal)
    postRequest.mockRejectedValue(refusal)
    await expect(checkApprove({ docId: AC, choice: 'merge' })).rejects.toBe(refusal)
  })
})

describe('check API — Git state normalization', () => {
  it('an awaiting slot: every field camelCase, the merge target is the default target', () => {
    expect(toCheckGitState({ ok: true, state: awaitingState() })).toEqual({
      groupId: GROUP,
      status: 'awaiting_choice',
      branch: 'test2_default_0034',
      baseBranch: 'main',
      workBaseRef: 'main',
      defaultAction: 'merge',
      choices: ['merge', 'merge_only', 'wait'],
      aheadCount: 2,
      behindCount: 0,
      mergeCommit: null,
      approvalPending: false,
      approvalInFlight: false,
      approvalJob: null,
    })
  })

  it('the none state (no Git work): no choices, nothing to show beyond the status', () => {
    const state = toCheckGitState({ ok: true, state: noneState })
    expect(state.status).toBe('none')
    expect(state.choices).toEqual([])
    expect(state.workBaseRef).toBeNull()
    expect(state.aheadCount).toBeNull()
  })

  it('choices keep the server order, drop unknown values (aux push is not offered here)', () => {
    expect(toCheckGitState({ state: awaitingState({ choices: ['wait', 'push', 'merge', 7] }) }).choices).toEqual(['merge', 'wait'])
  })

  it('a merged slot carries its merge commit; a work base other than the base is the target', () => {
    const state = toCheckGitState({ state: awaitingState({ status: 'merged', choices: [], merge_commit: 'abcdef0123456789', default_target: 'release/1.2' }) })
    expect(state.mergeCommit).toBe('abcdef0123456789')
    expect(state.workBaseRef).toBe('release/1.2')
  })

  it('approval_in_flight: true / false / missing or null -> unknown (null)', () => {
    expect(toCheckGitState({ state: awaitingState({ approval_in_flight: true }) }).approvalInFlight).toBe(true)
    expect(toCheckGitState({ state: awaitingState({ approval_in_flight: null }) }).approvalInFlight).toBeNull()
    const { approval_in_flight: _dropped, ...without } = awaitingState()
    expect(toCheckGitState({ state: without }).approvalInFlight).toBeNull()
  })

  it('the approval job view and a pending approval', () => {
    const state = toCheckGitState({
      state: awaitingState({
        approval_pending: true,
        approval_in_flight: true,
        approval_job: { job_id: 'job_7', status: 'blocked', stage: 'freeze_wait', doc_id: AC, last_error_code: null, blocker: { domain: 'merge_target' } },
      }),
    })
    expect(state.approvalPending).toBe(true)
    expect(state.approvalJob).toEqual({ jobId: 'job_7', status: 'blocked', stage: 'freeze_wait', docId: AC })
  })

  it('a bare state object (no envelope) is read the same; an empty answer is none', () => {
    expect(toCheckGitState(awaitingState()).status).toBe('awaiting_choice')
    expect(toCheckGitState(null).status).toBe('none')
    expect(toCheckGitState({ ok: true }).choices).toEqual([])
  })
})

describe('check API — final approval answers (approval_publish.response_payload)', () => {
  it('executed: approved, the Git result as sent', () => {
    const result = toCheckApproveResult({
      ok: true,
      git: { ok: true, terminal: true, result: { action: 'merge', status: 'merged', merge_commit: 'abcdef0123456789', pushed: true, conflict_files: [] } },
      approval: { approved: true, document_status: 'approved', root_status: 'wf_done', stage: 'complete', deferred: false },
      job: { job_id: 'job_1', status: 'succeeded' },
      document: docRow('approved'),
    })
    expect(result).toEqual({
      approved: true,
      stage: null,
      jobId: 'job_1',
      git: { ok: true, quiet: false, status: 'merged', mergeCommit: 'abcdef0123456789', pushed: true, conflictFiles: 0, errorCode: null },
      document: expect.objectContaining({ docId: AC, docReviewStatus: 'approved' }),
    })
  })

  it('queued: not approved yet, stage and job id', () => {
    const result = toCheckApproveResult({
      ok: true,
      response_status: 'queued',
      git: { ok: true, terminal: false, queued: true },
      approval: { approved: false, document_status: 'pending_review', root_status: 'wf_in_progress', deferred: true, stage: 'queued', job_id: 'job_2', blocker: { domain: 'merge_target' } },
      job: { job_id: 'job_2', status: 'blocked' },
    })
    expect(result.approved).toBe(false)
    expect(result.stage).toBe('queued')
    expect(result.jobId).toBe('job_2')
    expect(result.git?.status).toBeNull()
    expect(result.document).toBeNull()
  })

  it('handed off to a conflict review: stage git_finalize, the conflict files counted', () => {
    const result = toCheckApproveResult({
      ok: true,
      git: { ok: true, result: { status: 'conflict', merge_id: 12, conflict_files: ['a.py', 'b.py'] } },
      approval: { approved: false, stage: 'git_finalize', deferred: true, merge_id: 12 },
      job: { job_id: 'job_3' },
    })
    expect(result.stage).toBe('git_finalize')
    expect(result.git).toEqual({ ok: true, quiet: false, status: 'conflict', mergeCommit: null, pushed: null, conflictFiles: 2, errorCode: null })
  })

  it('the plain transition (no git_action): approved when the document says so', () => {
    expect(toCheckApproveResult({ document: docRow('approved') }).approved).toBe(true)
    expect(toCheckApproveResult({ document: docRow('pending_review') }).approved).toBe(false)
    expect(toCheckApproveResult({ document: docRow('approved') }).git).toBeNull()
  })

  it('a failed Git step beside a standing approval, a quiet no-op', () => {
    const failed = toCheckApproveResult({ ok: true, git: { ok: false, error: { code: 'base_dirty' } }, approval: { approved: true }, document: docRow('approved') })
    expect(failed.approved).toBe(true)
    expect(failed.git).toMatchObject({ ok: false, errorCode: 'base_dirty' })
    expect(toCheckApproveResult({ git: { ok: true, quiet: true }, approval: { approved: true } }).git?.quiet).toBe(true)
  })

  it('a document without doc_id is ignored, not thrown', () => {
    expect(toCheckApproveResult({ document: { title: 'x' }, approval: { approved: true } })).toMatchObject({ approved: true, document: null })
  })
})

describe('check API — reading a refusal', () => {
  it.each<[string, unknown, CheckErrorKind, number | null, string | null]>([
    ['423 mutation policy', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }), 'running', 423, 'GROUP_AI_RUN_LOCKED'],
    ['409 group_disposed detail', axiosError(409, { detail: `group_disposed: ${AC}` }), 'disposed', 409, null],
    ['409 git_busy (approval retry)', axiosError(409, { ok: false, error: { code: 'git_busy', message: 'x' } }), 'git_busy', 409, 'git_busy'],
    ['409 merge_target_busy', axiosError(409, { ok: false, error: { code: 'merge_target_busy', details: {} }, approval: {} }), 'git_busy', 409, 'merge_target_busy'],
    ['422 invalid_request (stale Git choice)', axiosError(422, { ok: false, error: { code: 'invalid_request' } }), 'stale_choice', 422, 'invalid_request'],
    ['409 approval_precheck_failed', axiosError(409, { ok: false, error: { code: 'approval_precheck_failed' } }), 'precheck', 409, 'approval_precheck_failed'],
    ['409 final_approval_retry_mismatch', axiosError(409, { ok: false, error: { code: 'final_approval_retry_mismatch' } }), 'precheck', 409, 'final_approval_retry_mismatch'],
    ['403 precheck permission', axiosError(403, { ok: false, error: { code: 'approval_precheck_failed' } }), 'forbidden', 403, 'approval_precheck_failed'],
    ['403 detail', axiosError(403, { detail: 'document.approve permission required' }), 'forbidden', 403, null],
    ['409 other transition', axiosError(409, { detail: 'Invalid review transition' }), 'conflict', 409, null],
    ['404', axiosError(404, { detail: 'Document not found' }), 'not_found', 404, null],
    ['422 other', axiosError(422, { detail: [] }), 'invalid', 422, null],
    ['500', axiosError(500, {}), 'other', 500, null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null],
  ])('%s -> %s', (_label, error, kind, status, code) => {
    expect(checkErrorOf(error)).toEqual({ kind, status, code })
  })
})
