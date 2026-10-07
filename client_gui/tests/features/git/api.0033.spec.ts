import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0033 — features/git/api.ts: request shapes of the existing Git contracts and
// the snake_case-to-model boundary (0033-D §4.4).
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({ getRequest, postRequest }))

import {
  GitRequestError,
  abortMerge,
  approveReview,
  baseCommit,
  baseRemove,
  baseRevert,
  cancelAiRun,
  fetchConflicts,
  fetchProviders,
  fetchReview,
  finalizeGroup,
  gitErrorOf,
  pushBranch,
  rejectReview,
  startConflictAi,
} from '@/features/git/api'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

const axiosError = (status: number, data: unknown) => Object.assign(new Error('x'), { isAxiosError: true, response: { status, data } })

describe('reads', () => {
  it('conflicts: files with marker counts, content dropped', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        merge_id: 7,
        branch: 'flow',
        base_branch: 'main',
        target_branch: 'v0.2',
        files: [{ path: 'a\\b.ts', content: '<<<<<<< x', conflict_count: 1 }, { path: 'c.ts', content: '', conflict_count: 0 }],
      },
    })
    await expect(fetchConflicts('p.m.0001', 7)).resolves.toEqual({
      mergeId: 7,
      branch: 'flow',
      baseBranch: 'main',
      targetBranch: 'v0.2',
      trConflict: false,
      files: [
        { path: 'a/b.ts', conflictCount: 1 },
        { path: 'c.ts', conflictCount: 0 },
      ],
    })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge/7/conflicts')
  })

  it('conflicts: a TR revert / reapply session is marked from kind and tr_conflict', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        merge_id: 9,
        kind: 'tr_revert',
        target_branch: null,
        tr_conflict: { doc_id: 'p.m.0001.0003-TR', review_state: 'open' },
        files: [{ path: 'a.ts', conflict_count: 1 }],
      },
    })
    await expect(fetchConflicts('p.m.0001', 9)).resolves.toMatchObject({ mergeId: 9, trConflict: true })
    getRequest.mockResolvedValue({ data: { ok: true, merge_id: 9, kind: 'tr_reapply', files: [] } })
    await expect(fetchConflicts('p.m.0001', 9)).resolves.toMatchObject({ trConflict: true })
    getRequest.mockResolvedValue({ data: { ok: true, merge_id: 9, kind: 'merge', tr_conflict: null, files: [] } })
    await expect(fetchConflicts('p.m.0001', 9)).resolves.toMatchObject({ trConflict: false })
  })

  it('review: only what the card uses', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        result: {
          review_state: 're_review',
          review_fingerprint: 'fp',
          changes: [{ path: 'a.ts', status: 'M', insertions: 1 }],
          resolver_provider: 'Opus',
          reconciliation_kind: 'push_rejected',
          last_error: { code: 'x', files: ['secret'] },
          can_approve: true,
          can_reject: false,
        },
      },
    })
    await expect(fetchReview('p.m.0001', 7)).resolves.toEqual({
      mergeId: 7,
      reviewState: 're_review',
      fingerprint: 'fp',
      changes: [{ path: 'a.ts', status: 'M' }],
      resolverProvider: 'Opus',
      reconciliationKind: 'push_rejected',
      canApprove: true,
      canReject: false,
    })
  })

  it('providers: ids and the default', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, providers: [{ id: 'a', name: 'A' }, null, { id: '' }], default_provider_id: 'a' } })
    await expect(fetchProviders('p')).resolves.toEqual({ providerIds: ['a'], defaultProviderId: 'a' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/providers', { project: 'p' })
  })
})

describe('actions', () => {
  it('AI start is a single resolve_conflict run with merge_id, no provider, no auto', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'r1' } })
    await expect(startConflictAi('p.m.0001', 7)).resolves.toBe('r1')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/start', {
      project: 'p',
      module: 'm',
      group: '0001',
      action_scope: 'resolve_conflict',
      mode: 'single',
      merge_id: 7,
    })
  })

  it('cancel / abort paths', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, status: 'finished' } })
    await expect(cancelAiRun('r 1')).resolves.toBe('finished')
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/ai-invoke/r%201/cancel', {})
    await abortMerge('p.m.0001', 7)
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/merge/7/abort', {})
  })

  it('approve / reject bodies', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'commit_creation_failed', review_state: 're_review', errors: [{}, {}] } } })
    await expect(approveReview('p.m.0001', 7, 'uuid', 'fp')).resolves.toEqual({
      status: 'commit_creation_failed',
      reviewState: 're_review',
      reconciliationKind: null,
      errorCount: 2,
    })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/merge/7/approve', { attempt_id: 'uuid', review_fingerprint: 'fp' })
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'returned_to_resolver', resolver_run_id: 'r2' } } })
    await expect(rejectReview('p.m.0001', 7, 'why', 'opus')).resolves.toEqual({ status: 'returned_to_resolver', resolverRunId: 'r2' })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/merge/7/reject', {
      reason: 'why',
      provider_id: 'opus',
      provider_pinned: true,
    })
  })

  it('finalize sends the explicit action; the target only for merge / merge_only', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'merged' } } })
    await finalizeGroup('p.m.0001', 'merge', 'main')
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/finalize', { action: 'merge', git_target_branch: 'main' })
    await finalizeGroup('p.m.0001', 'push', 'main')
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/finalize', { action: 'push' })
    await finalizeGroup('p.m.0001', 'merge_only', null)
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/finalize', { action: 'merge_only' })
    await pushBranch('p', 'v0.2')
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/push', { branch: 'v0.2' })
  })

  it('base commit sends paths, the message only when not blank', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { committed: true, remaining: [], remaining_untracked: ['x'] } } })
    await expect(baseCommit('p', '  ', ['a.ts', 'n.md'])).resolves.toEqual({ committed: true, remaining: [], remainingUntracked: ['x'] })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/base-commit', { paths: ['a.ts', 'n.md'] })
    await baseCommit('p', ' fix ', ['a.ts'])
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/base-commit', { paths: ['a.ts'], message: 'fix' })
  })

  it('base revert / remove: an ok:false answer with per-file results is a result, not a failure', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: false, result: { results: [{ path: 'a.ts', result: 'error' }], remaining: ['a.ts'] } } })
    await expect(baseRevert('p', ['a.ts'])).resolves.toEqual({ results: [{ path: 'a.ts', result: 'error' }], remaining: ['a.ts'] })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/base-revert', { files: ['a.ts'] })
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { results: [{ path: 'n.md', result: 'removed' }], remaining_untracked: [] } } })
    await expect(baseRemove('p', ['n.md'])).resolves.toEqual({ results: [{ path: 'n.md', result: 'removed' }], remainingUntracked: [] })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/base-remove', { files: ['n.md'] })
  })
})

describe('errors', () => {
  it('4xx/5xx envelopes become GitRequestError with code and status', async () => {
    postRequest.mockRejectedValue(axiosError(500, { ok: false, error: { code: 'push_rejected', message: 'Git push was rejected' } }))
    const error = await finalizeGroup('p.m.0001', 'merge', null).catch((e) => e)
    expect(error).toBeInstanceOf(GitRequestError)
    expect(gitErrorOf(error)).toMatchObject({ code: 'push_rejected', status: 500, network: false })
    expect(error.message).not.toContain('Git push was rejected')
  })

  it('an HTTP 200 ok:false envelope is a failure too', async () => {
    postRequest.mockResolvedValue({ status: 200, data: { ok: false, error: { code: 'invalid_state' } } })
    const error = await abortMerge('p.m.0001', 7).catch((e) => e)
    expect(gitErrorOf(error)).toMatchObject({ code: 'invalid_state', status: 200 })
  })

  it('a request without a response is a network failure', async () => {
    getRequest.mockRejectedValue(Object.assign(new Error('Network Error'), { isAxiosError: true }))
    const error = await fetchReview('p.m.0001', 7).catch((e) => e)
    expect(gitErrorOf(error).network).toBe(true)
  })

  it('reads the transport error a features/project loader keeps as cause', () => {
    const wrapped = Object.assign(new Error('Failed'), { cause: axiosError(403, { ok: false, error: { code: 'forbidden' } }) })
    expect(gitErrorOf(wrapped)).toMatchObject({ code: 'forbidden', status: 403 })
  })
})
