import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02test.0052 0008 — features/git/api.ts: what the recovery screens read from the server: the session's recorded
// abort failure, the files resolved along with it, git's reason of a refused request, and the administrator's forced cleanup.
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({ getRequest, postRequest }))

import {
  abortFailureOf,
  abortMerge,
  fetchAbortFailures,
  fetchConflicts,
  fetchForceCleanupPreview,
  forceCleanup,
  gitErrorOf,
  gitReasonOf,
} from '@/features/git/api'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

const axiosError = (status: number, data: unknown) => Object.assign(new Error('x'), { isAxiosError: true, response: { status, data } })

describe('conflicts: abort_failure and companion_files', () => {
  it('keeps the recorded abort failure and the moved files resolved together', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true, merge_id: 7, files: [{ path: 'a.ts', conflict_count: 1 }], resolved_paths: [],
        abort_failure: { trigger: 'user_abort', reason: "error: Entry 'a.ts' not uptodate. Cannot merge.", returncode: 128, at: '2026-11-19T10:09:58+09:00', instance_id: 'x' },
        companion_files: [{ path: 'resources\\a.ts', source_path: 'a.ts' }, { path: 'only.ts' }, { nope: 1 }],
      },
    })
    await expect(fetchConflicts('p.m.0001', 7)).resolves.toMatchObject({
      abortFailure: { trigger: 'user_abort', reason: "error: Entry 'a.ts' not uptodate. Cannot merge.", returncode: 128, at: '2026-11-19T10:09:58+09:00' },
      companionFiles: [{ path: 'resources/a.ts', sourcePath: 'a.ts' }, { path: 'only.ts', sourcePath: null }],
    })
  })
  it('reads absent / malformed answers as none', () => {
    expect(abortFailureOf(null)).toBeNull()
    expect(abortFailureOf([])).toBeNull()
    expect(abortFailureOf('x')).toBeNull()
    expect(abortFailureOf({})).toEqual({ trigger: null, reason: null, returncode: null, at: null })
  })
})

describe('git\'s reason of a refused request', () => {
  const reason = "error: Entry 'a.ts' not uptodate. Cannot merge."
  it('is read from error.details.reason, else error.params.reason', async () => {
    postRequest.mockRejectedValue(axiosError(500, { ok: false, error: { code: 'git_error', details: { reason, recovery: 'force_cleanup' }, params: { reason: 'other' } } }))
    const first = await abortMerge('p.m.0001', 7).catch((error) => gitErrorOf(error))
    expect(gitReasonOf(first as never)).toBe(reason)
    expect((first as { details: { recovery?: string } }).details.recovery).toBe('force_cleanup')
    postRequest.mockRejectedValue(axiosError(500, { ok: false, error: { code: 'git_error', params: { reason } } }))
    expect(gitReasonOf(await abortMerge('p.m.0001', 7).catch((error) => gitErrorOf(error)) as never)).toBe(reason)
  })
  it('is null when the server gave none', async () => {
    postRequest.mockRejectedValue(axiosError(500, { ok: false, error: { code: 'git_error' } }))
    expect(gitReasonOf(await abortMerge('p.m.0001', 7).catch((error) => gitErrorOf(error)) as never)).toBeNull()
  })
})

describe('administrator forced cleanup', () => {
  it('reads the preview (read only GET) as the model', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, result: {
      merge_id: 7, workspace: '/w/feature_v0.2.0-57fd', target_branch: 'feature/v0.2.0', head: '81d1e0a9', baseline_head: '81d1e0a9', head_matches_baseline: true,
      discarded_paths: ['a\\b.ts'], abort_failure: { reason: 'r' }, group_branch_untouched: true,
    } } })
    await expect(fetchForceCleanupPreview('p.m.0001', 7)).resolves.toEqual({
      mergeId: 7, workspace: '/w/feature_v0.2.0-57fd', targetBranch: 'feature/v0.2.0', head: '81d1e0a9', baselineHead: '81d1e0a9', headMatchesBaseline: true,
      discardedPaths: ['a/b.ts'], abortFailure: { trigger: null, reason: 'r', returncode: null, at: null }, groupBranchUntouched: true,
    })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge/7/force-cleanup')
    expect(postRequest).not.toHaveBeenCalled()
  })
  it('POSTs the cleanup to the same path and reads the end state', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'aborted', forced: true, workspace: '/w', target_branch: 'main', reset_head: 'abc', discarded_paths: ['x'], branch_preserved: true } } })
    await expect(forceCleanup('p.m.0001', 7)).resolves.toEqual({ status: 'aborted', forced: true, workspace: '/w', targetBranch: 'main', resetHead: 'abc', discardedPaths: ['x'], branchPreserved: true })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge/7/force-cleanup', {})
  })
  it('a refusal (403 for a non-administrator, 409 head moved) is a Git error with its code', async () => {
    postRequest.mockRejectedValue(axiosError(409, { ok: false, error: { code: 'force_cleanup_head_moved' } }))
    expect(gitErrorOf(await forceCleanup('p.m.0001', 7).catch((error) => error))).toMatchObject({ code: 'force_cleanup_head_moved', status: 409 })
    postRequest.mockRejectedValue(axiosError(403, { ok: false, error: { code: 'forbidden' } }))
    expect(gitErrorOf(await forceCleanup('p.m.0001', 7).catch((error) => error))).toMatchObject({ code: 'forbidden', status: 403 })
  })
  it('lists the sessions marked "정리 실패" of a project', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, result: { project_id: 'p', sessions: [
      { merge_id: 457, group_id: 'p.m.0036', kind: 'merge', target_branch: 'feature/v0.2.0', abort_failure: { reason: 'r', at: 't' }, touched_at: 'x' },
      { group_id: 'no merge id' },
    ] } } })
    await expect(fetchAbortFailures('p')).resolves.toEqual([
      { mergeId: 457, groupId: 'p.m.0036', kind: 'merge', targetBranch: 'feature/v0.2.0', abortFailure: { trigger: null, reason: 'r', returncode: null, at: 't' }, touchedAt: 'x' },
    ])
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/merge-abort-failures')
  })
})
