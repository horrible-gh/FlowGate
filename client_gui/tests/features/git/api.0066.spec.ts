import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0066 T#5 — features/git/api.ts "Group worktree" section: the E1–E12 request
// shapes and the snake_case-to-model boundary (0066 D#2 §5-1, C1).
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({ getRequest, postRequest }))

import {
  GitRequestError,
  commitWorktree,
  fetchManualMerge,
  fetchMergeResults,
  fetchRefs,
  fetchWorktreeStatus,
  pushMergeResult,
  removeWorktreeFiles,
  requestManualMerge,
  revertWorktree,
  stageWorktree,
  suggestWorktreeCommitMessage,
  unstageWorktree,
  worktreeErrorOf,
} from '@/features/git/api'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

const G = 'p.m.0001'
const SERVER_TEXT = 'server text never kept'
const ok = (result: unknown) => ({ data: { ok: true, result } })
const axiosError = (status: number, data: unknown) => Object.assign(new Error('x'), { isAxiosError: true, response: { status, data } })

// One E1 answer the way worktree_manual.worktree_status builds it.
function rawStatus(overrides: Record<string, unknown> = {}) {
  return {
    group_id: G,
    branch: 'flowgate_v02_0008',
    head: 'h1',
    worktree_available: true,
    worktree_token: 'wt-1',
    index_token: 'ix-1',
    git_state: { status: 'waiting', root_wf_done: true, final_approval_bound: false },
    floor: {
      work_base_ref: 'v0.2',
      work_base_sha: 'f1',
      work_base_sync_sha: null,
      work_base_state: 'verified',
      scope_base_sha: 'f1',
      error: null,
    },
    committed: { commit_count: 2, changed_file_count: 3 },
    files: [
      {
        path: 'src\\a.ts',
        in_head: true,
        staged: 'M',
        unstaged: null,
        untracked: false,
        insertions: null,
        deletions: null,
        class: 'normal',
        protected_reason: null,
        unsupported: null,
        can_stage: true,
        can_unstage: true,
        can_revert: true,
        can_remove: false,
      },
      // A row that leaves out every permission: none of them may read as allowed.
      { path: 'docs/n.md', in_head: false, staged: null, unstaged: null, untracked: true, class: 'excluded', protected_reason: 'excluded_new' },
      { path: '', staged: 'M' },
    ],
    counts: {
      changed: 2,
      staged: 2,
      unstaged: 0,
      untracked: 1,
      excluded_new_untracked: 4,
      hidden: 1,
      managed_dirty: 0,
      unsupported: 0,
    },
    ownership: { available: true, reason: null },
    blockers: [{ code: 'session_open', operations: ['stage', 'commit'] }, { operations: ['stage'] }],
    ...overrides,
  }
}

describe('E1 worktree/status', () => {
  it('maps every field, keeps the server counts as they are and lists only well-formed files', async () => {
    getRequest.mockResolvedValue(ok(rawStatus()))
    const status = await fetchWorktreeStatus(G)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/status')
    expect(status).toEqual({
      groupId: G,
      branch: 'flowgate_v02_0008',
      head: 'h1',
      worktreeAvailable: true,
      worktreeToken: 'wt-1',
      indexToken: 'ix-1',
      gitState: { status: 'waiting', rootWfDone: true, finalApprovalBound: false },
      floor: {
        workBaseRef: 'v0.2',
        scopeBaseSha: 'f1',
        workBaseSha: 'f1',
        workBaseSyncSha: null,
        workBaseState: 'verified',
        error: null,
      },
      committed: { commitCount: 2, changedFileCount: 3 },
      files: [
        {
          path: 'src/a.ts',
          inHead: true,
          staged: 'M',
          unstaged: null,
          untracked: false,
          fileClass: 'normal',
          protectedReason: null,
          unsupported: null,
          canStage: true,
          canUnstage: true,
          canRevert: true,
          canRemove: false,
          insertions: null,
          deletions: null,
        },
        {
          path: 'docs/n.md',
          inHead: false,
          staged: null,
          unstaged: null,
          untracked: true,
          fileClass: 'excluded',
          protectedReason: 'excluded_new',
          unsupported: null,
          canStage: false,
          canUnstage: false,
          canRevert: false,
          canRemove: false,
          insertions: null,
          deletions: null,
        },
      ],
      // Server values: staged 2 with one listed staged file, hidden 1 — not recounted.
      counts: {
        changed: 2,
        staged: 2,
        unstaged: 0,
        untracked: 1,
        excludedNewUntracked: 4,
        hidden: 1,
        managedDirty: 0,
        unsupported: 0,
      },
      ownership: { available: true, reason: null },
      blockers: [{ code: 'session_open', operations: ['stage', 'commit'] }],
    })
  })

  it('hidden-only stage: no listed file, the counts still say what is staged', async () => {
    getRequest.mockResolvedValue(ok(rawStatus({ files: [], counts: { changed: 0, staged: 1, hidden: 1 } })))
    const status = await fetchWorktreeStatus(G)
    expect(status.files).toEqual([])
    expect(status.counts).toMatchObject({ changed: 0, staged: 1, hidden: 1 })
    // Not sent = unknown, never 0.
    expect(status.counts.unstaged).toBeNull()
    expect(status.counts.managedDirty).toBeNull()
  })

  it('an unknown letter is no letter; unknown class / reason strings are kept as is', async () => {
    getRequest.mockResolvedValue(
      ok(rawStatus({ files: [{ path: 'x.ts', staged: 'R', unstaged: '?', class: 'future_class', protected_reason: 'future', can_stage: 'yes' }] })),
    )
    const [file] = (await fetchWorktreeStatus(G)).files
    expect(file).toMatchObject({ staged: null, unstaged: null, fileClass: 'future_class', protectedReason: 'future', canStage: false })
  })

  it('a floor 409 in the body keeps its code and allowed details only, never the message', async () => {
    getRequest.mockResolvedValue(
      ok(
        rawStatus({
          committed: null,
          floor: {
            work_base_ref: 'v0.2',
            error: {
              code: 'group_work_base_diverged',
              message: SERVER_TEXT,
              details: { reason: 'diverged', paths: ['secret/x'], diagnostic: SERVER_TEXT },
            },
          },
        }),
      ),
    )
    const status = await fetchWorktreeStatus(G)
    expect(status.committed).toBeNull()
    expect(status.floor).toMatchObject({ workBaseRef: 'v0.2', scopeBaseSha: null, workBaseState: null })
    expect(status.floor.error?.code).toBe('group_work_base_diverged')
    expect(status.floor.error?.details.reason).toBe('diverged')
    expect(Object.keys(status.floor.error?.details ?? {}).sort()).toEqual(
      ['count', 'counts', 'jobId', 'latestMergeId', 'mergeId', 'protectedUncommitted', 'pushVia', 'reason', 'sessionKind', 'targetFastForwarded'].sort(),
    )
    expect(JSON.stringify(status)).not.toContain(SERVER_TEXT)
    expect(JSON.stringify(status)).not.toContain('secret/x')
  })

  it('no workspace: available false, no tokens, empty lists', async () => {
    getRequest.mockResolvedValue(
      ok({
        group_id: G,
        branch: null,
        head: null,
        worktree_available: false,
        worktree_token: null,
        index_token: null,
        git_state: { status: 'none' },
        floor: {},
        committed: null,
        files: [],
        counts: {},
        ownership: { available: false, reason: null },
        blockers: [{ code: 'worktree_unavailable', operations: ['stage'] }],
      }),
    )
    const status = await fetchWorktreeStatus(G)
    expect(status).toMatchObject({ worktreeAvailable: false, worktreeToken: null, indexToken: null, files: [], committed: null })
    expect(status.blockers).toEqual([{ code: 'worktree_unavailable', operations: ['stage'] }])
  })
})

describe('E2 / E3 stage, unstage', () => {
  const answer = (overrides: Record<string, unknown> = {}) =>
    ok({ applied: ['src\\a.ts'], applied_hidden_count: 0, skipped: { managed: 1, hidden: 2, excluded_new: 3, unsupported: 4 }, status: rawStatus(), status_error: null, ...overrides })

  it('selected paths with the screen token; the refreshed status comes back as a model', async () => {
    postRequest.mockResolvedValue(answer())
    const result = await stageWorktree(G, { paths: ['src\\a.ts'] }, 'wt-1')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/stage', { paths: ['src/a.ts'], worktree_token: 'wt-1' })
    expect(result.applied).toEqual(['src/a.ts'])
    expect(result.skipped).toEqual({ managed: 1, hidden: 2, excludedNew: 3, unsupported: 4 })
    expect(result.status?.files[0].path).toBe('src/a.ts')
    expect(result.statusError).toBeNull()
  })

  it('all: only `all: true` goes out — no paths; no token when none is given', async () => {
    postRequest.mockResolvedValue(answer({ applied: [], applied_hidden_count: 1 }))
    const result = await unstageWorktree(G, { all: true }, null)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/unstage', { all: true })
    expect(result).toMatchObject({ applied: [], appliedHiddenCount: 1 })
  })

  it('a change that worked but whose status refresh failed: null status with the server code', async () => {
    postRequest.mockResolvedValue(answer({ status: null, status_error: 'ownership_unavailable' }))
    await expect(stageWorktree(G, { all: true }, 'wt-1')).resolves.toMatchObject({ status: null, statusError: 'ownership_unavailable' })
    postRequest.mockResolvedValue(answer({ status: null, status_error: undefined, skipped: undefined }))
    await expect(stageWorktree(G, { all: true }, 'wt-1')).resolves.toMatchObject({
      status: null,
      statusError: 'status_unavailable',
      skipped: { managed: 0, hidden: 0, excludedNew: 0, unsupported: 0 },
    })
  })

  it('worktree_changed is a GitRequestError; details keep the allowed keys only', async () => {
    postRequest.mockRejectedValue(
      axiosError(409, { ok: false, error: { code: 'worktree_changed', message: SERVER_TEXT, details: { reason: 'token', paths: [{ path: 'x', reason: 'managed' }] } } }),
    )
    const error = await stageWorktree(G, { paths: ['x'] }, 'wt-0').catch((e) => e)
    expect(error).toBeInstanceOf(GitRequestError)
    expect(error.message).toBe('worktree_changed')
    const failure = worktreeErrorOf(error)
    expect(failure).toMatchObject({ code: 'worktree_changed', httpStatus: 409, network: false })
    expect(failure.details.reason).toBe('token')
    expect(JSON.stringify(failure)).not.toContain(SERVER_TEXT)
    expect(JSON.stringify(failure)).not.toContain('managed')
  })
})

describe('E4 commit, E5 revert, E6 remove, E7 suggest', () => {
  it('commit: the message as typed plus the index token the confirmation showed', async () => {
    postRequest.mockResolvedValue(
      ok({ commit_sha: 'c'.repeat(40), commit: 'ccccccc', subject: 'fix: a', committed_paths: ['src\\a.ts'], committed_count: 1, status: rawStatus(), status_error: null }),
    )
    const result = await commitWorktree(G, ' fix: a ', 'ix-1')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/commit', { message: ' fix: a ', index_token: 'ix-1' })
    expect(result).toMatchObject({ commitSha: 'c'.repeat(40), commit: 'ccccccc', subject: 'fix: a', committedPaths: ['src/a.ts'], committedCount: 1, statusError: null })
  })

  it('commit refusals carry the per-class counts', async () => {
    postRequest.mockRejectedValue(
      axiosError(409, { ok: false, error: { code: 'staged_protected_paths', message: SERVER_TEXT, details: { counts: { hidden: 1, managed: 0, bogus: 'x' }, paths: [] } } }),
    )
    const failure = worktreeErrorOf(await commitWorktree(G, 'm', null).catch((e) => e))
    expect(failure.code).toBe('staged_protected_paths')
    expect(failure.details.counts).toEqual({ hidden: 1, managed: 0 })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/commit', { message: 'm' })
  })

  it('revert / remove: per-path results', async () => {
    postRequest.mockResolvedValue(ok({ results: [{ path: 'src\\a.ts', result: 'reverted' }, { result: 'x' }], status: rawStatus(), status_error: null }))
    await expect(revertWorktree(G, ['src\\a.ts'], 'wt-1')).resolves.toMatchObject({ results: [{ path: 'src/a.ts', result: 'reverted' }] })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/worktree/revert', { paths: ['src/a.ts'], worktree_token: 'wt-1' })
    postRequest.mockResolvedValue(ok({ results: [{ path: 'n.md', result: 'removed' }], status: rawStatus(), status_error: null }))
    await expect(removeWorktreeFiles(G, ['n.md'], null)).resolves.toMatchObject({ results: [{ path: 'n.md', result: 'removed' }] })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/groups/p.m.0001/git/worktree/remove', { paths: ['n.md'] })
  })

  it('suggest: the draft and the snapshot it saw; nothing else is called', async () => {
    postRequest.mockResolvedValue(ok({ suggested: 'feat(git): x', source: 'ai', provider_id: 'opus', staged_count: 3, input_truncated: true, head: 'h1', index_token: 'ix-1' }))
    await expect(suggestWorktreeCommitMessage(G, 'opus')).resolves.toEqual({
      suggested: 'feat(git): x',
      providerId: 'opus',
      stagedCount: 3,
      inputTruncated: true,
      head: 'h1',
      indexToken: 'ix-1',
    })
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/worktree/commit-message/suggest', { provider_id: 'opus' })
  })
})

describe('E8 manual-merge view, E9 manual merge', () => {
  const view = {
    can_merge: false,
    blocked: [
      { code: 'uncommitted_changes', message: SERVER_TEXT, details: { count: 4, counts: { staged: 1, hidden: 1 }, protected_uncommitted: 1 } },
      { code: 'graph_execution_active', message: SERVER_TEXT, details: { reason: 'graph_execution_handoff' } },
      { message: 'no code' },
    ],
    admission_basis: 'wf_done',
    approval: { ac_doc_id: 'p.m.0001.0009-AC', status: 'pending_review', final_approval_bound: true },
    default_target: 'v0.2',
    targets: [
      { name: 'v0.2', kind: 'base', eligible: true, blocked_reason: null, is_work_base: true, unmerge_supported: false },
      { name: 'origin-only', kind: 'local', eligible: false, blocked_reason: 'remote_only', is_work_base: false },
      { kind: 'local' },
    ],
    preview: {
      target_branch: 'v0.2',
      work_base_ref: 'v0.2',
      project_base_branch: 'v0.2',
      is_work_base: true,
      is_project_base: true,
      unmerge_supported: true,
      incoming_work_base_commits: null,
      warnings: [],
    },
    commit_count: 2,
    floor_error: null,
    ahead_of_target: 3,
    merge_needed: true,
    recovery: false,
    uncommitted: { blocking: 4, counts: { staged: 1, hidden: 1 }, protected_uncommitted: 1, reason: null },
    graph: { run_present: true, blocker: 'graph_execution_handoff', gate_open: true },
  }

  it('maps the verdict; a block keeps code, reason and allowed details, never the message', async () => {
    getRequest.mockResolvedValue(ok(view))
    const result = await fetchManualMerge(G, null)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/manual-merge')
    expect(result.canMerge).toBe(false)
    expect(result.blocked.map((b) => [b.code, b.reason])).toEqual([
      ['uncommitted_changes', null],
      ['graph_execution_active', 'graph_execution_handoff'],
    ])
    expect(result.blocked[0].details).toMatchObject({ count: 4, counts: { staged: 1, hidden: 1 }, protectedUncommitted: 1 })
    expect(result.targets).toEqual([
      { name: 'v0.2', kind: 'base', eligible: true, blockedReason: null, isWorkBase: true },
      { name: 'origin-only', kind: 'local', eligible: false, blockedReason: 'remote_only', isWorkBase: false },
    ])
    expect(result).toMatchObject({
      admissionBasis: 'wf_done',
      approval: { acDocId: 'p.m.0001.0009-AC', status: 'pending_review', finalApprovalBound: true },
      defaultTarget: 'v0.2',
      preview: { targetBranch: 'v0.2', isWorkBase: true, isProjectBase: true, warnings: [], error: null },
      commitCount: 2,
      floorError: null,
      aheadOfTarget: 3,
      mergeNeeded: true,
      recovery: false,
      uncommitted: { blocking: 4, counts: { staged: 1, hidden: 1 }, protectedUncommitted: 1, reason: null },
      graph: { runPresent: true, blocker: 'graph_execution_handoff', gateOpen: true },
    })
    expect(JSON.stringify(result)).not.toContain(SERVER_TEXT)
  })

  it('a chosen target goes out as the `target` query; a failed preview keeps its code only', async () => {
    getRequest.mockResolvedValue(ok({ ...view, can_merge: 'true', preview: { error: { code: 'merge_target_not_found', message: SERVER_TEXT } }, ahead_of_target: null }))
    const result = await fetchManualMerge(G, 'release')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/manual-merge', { target: 'release' })
    expect(result.canMerge).toBe(false)
    expect(result.aheadOfTarget).toBeNull()
    expect(result.preview?.error?.code).toBe('merge_target_not_found')
    expect(JSON.stringify(result)).not.toContain(SERVER_TEXT)
  })

  it('merge: push flag and the target the screen fixed; conflict paths are counted, not kept', async () => {
    postRequest.mockResolvedValue(
      ok({
        action: 'merge',
        status: 'conflict',
        group_status: 'conflict',
        worktree_kept: true,
        merge_commit: null,
        merge_commit_sha: null,
        pushed: false,
        recovered: false,
        merge_id: 21,
        conflict_files: ['shared.txt'],
        hidden_conflict_count: 1,
        target_branch: 'release',
        unmerge_supported: false,
        excluded_artifact_count: 2,
        job_id: 'job-9',
      }),
    )
    const result = await requestManualMerge(G, 'release', true)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/manual-merge', { push: true, target_branch: 'release' })
    expect(result).toEqual({
      status: 'conflict',
      groupStatus: 'conflict',
      worktreeKept: true,
      action: 'merge',
      targetBranch: 'release',
      mergeCommit: null,
      mergeCommitSha: null,
      pushed: false,
      recovered: false,
      mergeId: 21,
      conflictFileCount: 2,
      excludedArtifactCount: 2,
      jobId: 'job-9',
    })
    expect(JSON.stringify(result)).not.toContain('shared.txt')
  })

  it('merge into the default target sends no target; a queued job keeps its id', async () => {
    postRequest.mockRejectedValue(axiosError(409, { ok: false, error: { code: 'git_busy', message: SERVER_TEXT, details: { job_id: 'job-3', phase: 'start' } } }))
    const failure = worktreeErrorOf(await requestManualMerge(G, null, false).catch((e) => e))
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/manual-merge', { push: false })
    expect(failure).toMatchObject({ code: 'git_busy', details: { jobId: 'job-3' } })
  })

  it('nothing_to_merge details: reason, merge id, push_via, fast-forward flag', async () => {
    postRequest.mockRejectedValue(
      axiosError(409, {
        ok: false,
        error: { code: 'nothing_to_merge', message: SERVER_TEXT, details: { reason: 'already_recorded', merge_id: 11, push_via: 'merge_result_push', target_fast_forwarded: true } },
      }),
    )
    const failure = worktreeErrorOf(await requestManualMerge(G, null, true).catch((e) => e))
    expect(failure.details).toMatchObject({ reason: 'already_recorded', mergeId: 11, pushVia: 'merge_result_push', targetFastForwarded: true })
  })
})

describe('E10 merge results, E11 follow-up push, E12 fetch-refs', () => {
  const row = (overrides: Record<string, unknown> = {}) => ({
    merge_id: 11,
    target: { target_branch: 'v0.2', target_kind: 'branch', is_project_base: true, managed_workspace: false, unmerge_supported: false },
    merge_commit: 'a'.repeat(40),
    pushed: false,
    recovered: true,
    completed_at: '2026-10-16T10:21:00+09:00',
    remote: { measured: true, remote_branch_missing: false, local_ahead_count: 1, contains_merge_commit: false },
    push_available: true,
    push_blocked_reason: null,
    ...overrides,
  })

  it('results: one row per target; a row without id or target is dropped', async () => {
    getRequest.mockResolvedValue(ok({ group_status: 'none', results: [row(), row({ merge_id: null }), row({ target: null })], push_candidates: 1, group_blocker: null }))
    const view = await fetchMergeResults(G)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge-result')
    expect(view).toEqual({
      groupStatus: 'none',
      results: [
        {
          mergeId: 11,
          target: { targetBranch: 'v0.2', isProjectBase: true },
          mergeCommit: 'a'.repeat(40),
          pushed: false,
          recovered: true,
          completedAt: '2026-10-16T10:21:00+09:00',
          remote: { measured: true, remoteBranchMissing: false, localAheadCount: 1, containsMergeCommit: false },
          pushAvailable: true,
          pushBlockedReason: null,
        },
      ],
      pushCandidates: 1,
      groupBlocker: null,
    })
  })

  it('an unmeasured remote reads as unmeasured, not as published', async () => {
    getRequest.mockResolvedValue(ok({ results: [row({ remote: { measured: false }, push_available: false, push_blocked_reason: 'unmeasured' })] }))
    const [result] = (await fetchMergeResults(G)).results
    expect(result.remote).toEqual({ measured: false, remoteBranchMissing: null, localAheadCount: null, containsMergeCommit: null })
    expect(result).toMatchObject({ pushAvailable: false, pushBlockedReason: 'unmeasured' })
  })

  it('push: only the record id and its merge commit go out, never a branch', async () => {
    postRequest.mockResolvedValue(
      ok({ pushed_ref: 'refs/heads/v0.2', pushed_head: 'b'.repeat(40), target_branch: 'v0.2', is_project_base: true, merge_id: 11, merge_commit: 'a'.repeat(40), already_published: true, phase: 'done', job_id: 'job-2' }),
    )
    const result = await pushMergeResult(G, 11, 'aaaaaaa')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge-result/push', { merge_id: 11, merge_commit: 'aaaaaaa' })
    expect(result).toEqual({
      pushedRef: 'refs/heads/v0.2',
      pushedHead: 'b'.repeat(40),
      targetBranch: 'v0.2',
      isProjectBase: true,
      mergeId: 11,
      mergeCommit: 'a'.repeat(40),
      alreadyPublished: true,
      jobId: 'job-2',
      phase: 'done',
    })
  })

  it('stale_target keeps the newer record id', async () => {
    postRequest.mockRejectedValue(axiosError(409, { ok: false, error: { code: 'stale_target', message: SERVER_TEXT, details: { reason: 'superseded', merge_id: 11, latest_merge_id: 12 } } }))
    const failure = worktreeErrorOf(await pushMergeResult(G, 11, 'aaaaaaa').catch((e) => e))
    expect(failure.details).toMatchObject({ reason: 'superseded', mergeId: 11, latestMergeId: 12 })
  })

  it('fetch-refs: the project path with the group id; the group remote state comes back', async () => {
    postRequest.mockResolvedValue(
      ok({
        fetched: true,
        base_branch: 'v0.2',
        ahead_count: 1,
        behind_count: 0,
        group: {
          group_id: G,
          work_base: { work_base_ref: 'v0.2', branch: 'v0.2', remote_branch_missing: false, ahead_count: 1, behind_count: 2 },
          results: [{ merge_id: 11, target: row().target, remote: row().remote, push_available: true, push_blocked_reason: null }],
          push_candidates: 1,
          group_blocker: null,
        },
      }),
    )
    const result = await fetchRefs('p', G)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/fetch-refs', { group_id: G })
    expect(result).toEqual({
      fetched: true,
      baseBranch: 'v0.2',
      aheadCount: 1,
      behindCount: 0,
      group: {
        groupId: G,
        workBaseRef: 'v0.2',
        workBase: { branch: 'v0.2', remoteBranchMissing: false, aheadCount: 1, behindCount: 2 },
        results: [
          {
            mergeId: 11,
            target: { targetBranch: 'v0.2', isProjectBase: true },
            remote: { measured: true, remoteBranchMissing: false, localAheadCount: 1, containsMergeCommit: false },
            pushAvailable: true,
            pushBlockedReason: null,
          },
        ],
        pushCandidates: 1,
        groupBlocker: null,
      },
    })
  })

  it('fetch-refs without a group: empty body, no group part', async () => {
    postRequest.mockResolvedValue(ok({ fetched: true, base_branch: 'v0.2', ahead_count: null, behind_count: null, group: null }))
    await expect(fetchRefs('p', null)).resolves.toEqual({ fetched: true, baseBranch: 'v0.2', aheadCount: null, behindCount: null, group: null })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/fetch-refs', {})
  })
})

describe('failure shapes', () => {
  it('an HTTP 200 `ok: false` fails like a 4xx; a network failure is marked as one', async () => {
    getRequest.mockResolvedValue({ status: 200, data: { ok: false, error: { code: 'invalid_state', message: SERVER_TEXT, details: { session_kind: 'merge' } } } })
    const failure = worktreeErrorOf(await fetchWorktreeStatus(G).catch((e) => e))
    expect(failure).toMatchObject({ code: 'invalid_state', httpStatus: 200, network: false, details: { sessionKind: 'merge' } })

    getRequest.mockRejectedValue(Object.assign(new Error('Network Error'), { isAxiosError: true }))
    const network = worktreeErrorOf(await fetchMergeResults(G).catch((e) => e))
    expect(network).toMatchObject({ code: null, network: true })
  })

  it('403 stays a code (forbidden) the screen can keep', async () => {
    postRequest.mockRejectedValue(axiosError(403, { ok: false, error: { code: 'forbidden', message: SERVER_TEXT } }))
    const failure = worktreeErrorOf(await stageWorktree(G, { all: true }, null).catch((e) => e))
    expect(failure).toMatchObject({ code: 'forbidden', httpStatus: 403 })
  })
})

describe('source guards of the Group worktree section', () => {
  const API = readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/api.ts'), 'utf8')
  const section = API.slice(API.indexOf('// ── Group worktree (flowgate.v02.0066)'))

  it('holds no branch name and calls no finalize / push / fetch / base contract', () => {
    expect(section.length).toBeGreaterThan(1000)
    expect(section).not.toMatch(/'v0\.2'|'main'|"main"/)
    const code = section.replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/\/finalize|\/git\/push|\/git\/fetch[`'/]|base-commit|base-revert|base-remove/)
  })
})
