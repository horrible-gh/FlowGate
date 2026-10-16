import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0066 T#5 — the features/project field mapping the Git tab's Group worktree area
// reads (0066 D#2 §4-2, C2 / C3): git/status ahead / behind / unpushed, the /changes scope floor,
// line counts and tool artifact count, and the transport error kept by /changes and /diff.
const getRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest: vi.fn(), extractApiErrorMessage }))

import { fetchFlowChangeSet, fetchFlowFileDiff, fetchProjectGitStatus } from '@/features/project/api'

beforeEach(() => {
  getRequest.mockReset()
  extractApiErrorMessage.mockClear()
})

const statusAnswer = (fields: Record<string, unknown>) => ({
  data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [], ...fields } },
})

describe('git/status: Base against its remote (C2)', () => {
  it('maps ahead / behind and a measured unpushed block with its merges', async () => {
    getRequest.mockResolvedValue(
      statusAnswer({
        ahead_count: 3,
        behind_count: 0,
        unpushed: {
          count: 2,
          commit_count: 3,
          measured: true,
          remote_branch_missing: false,
          local_commit_count: null,
          merges: [
            { merge_commit: 'abc1234', group_id: 'p.m.0001', subject: 'Merge flow', merged_at: '2026-10-16T10:00:00+09:00', can_unmerge: true, blocked_reason: null },
            { merge_commit: 'def5678', group_id: null, subject: 'Merge other', merged_at: null, can_unmerge: false, blocked_reason: 'unmapped' },
            { merge_commit: '', group_id: 'x' },
            null,
          ],
        },
      }),
    )
    const status = await fetchProjectGitStatus('p')
    expect(status?.aheadCount).toBe(3)
    expect(status?.behindCount).toBe(0)
    expect(status?.unpushed).toEqual({
      count: 2,
      commitCount: 3,
      measured: true,
      remoteBranchMissing: false,
      localCommitCount: null,
      merges: [
        { mergeCommit: 'abc1234', groupId: 'p.m.0001', subject: 'Merge flow', mergedAt: '2026-10-16T10:00:00+09:00', canUnmerge: true, blockedReason: null },
        // A merge the server could not map keeps its null group (unmapped), not dropped.
        { mergeCommit: 'def5678', groupId: null, subject: 'Merge other', mergedAt: null, canUnmerge: false, blockedReason: 'unmapped' },
      ],
    })
  })

  it('an unmeasured Base is never read as 0', async () => {
    getRequest.mockResolvedValue(
      statusAnswer({ unpushed: { count: 0, commit_count: 0, merges: [], measured: false, remote_branch_missing: true, local_commit_count: 5 } }),
    )
    const status = await fetchProjectGitStatus('p')
    expect(status?.unpushed).toEqual({ count: null, commitCount: null, measured: false, remoteBranchMissing: true, localCommitCount: 5, merges: [] })
  })

  it('a field the server sends without a number is null; Git off sends null counts', async () => {
    getRequest.mockResolvedValue({
      data: { ok: true, status: { enabled: false, base_branch: null, base_branch_configured: false, ahead_count: null, behind_count: null, slots: [] } },
    })
    const status = await fetchProjectGitStatus('p')
    expect(status?.aheadCount).toBeNull()
    expect(status?.behindCount).toBeNull()
    expect(status && 'unpushed' in status).toBe(false)

    getRequest.mockResolvedValue(statusAnswer({ ahead_count: '3', behind_count: Number.NaN, unpushed: 'x' }))
    const odd = await fetchProjectGitStatus('p')
    expect(odd).toMatchObject({ aheadCount: null, behindCount: null, unpushed: null })
  })

  it('a server without the fields leaves them out: unknown (never 0) and the 0024 shape intact', async () => {
    getRequest.mockResolvedValue(statusAnswer({}))
    const status = await fetchProjectGitStatus('p')
    expect(status?.aheadCount ?? null).toBeNull()
    expect(status?.behindCount ?? null).toBeNull()
    expect(status?.unpushed ?? null).toBeNull()
    expect(Object.keys(status ?? {}).sort()).toEqual(['baseBranch', 'baseDirty', 'baseUntracked', 'enabled', 'pending', 'slots'])
  })
})

describe('/changes: scope floor, line counts, tool artifacts (C3)', () => {
  it('maps the floor, the per-file line counts and the artifact count (no artifact path kept)', async () => {
    getRequest.mockResolvedValue({
      data: {
        data: {
          group_id: 'g',
          branch: 'flow-1',
          commit: 'abc',
          base_branch: 'v0.2',
          work_base_ref: 'v0.2',
          scope_base_sha: 'mb1',
          work_base_sha: 'wb1',
          work_base_sync_sha: null,
          work_base_state: 'verified',
          changes: [
            { path: 'src\\a.ts', status: 'M', insertions: 3, deletions: 1 },
            { path: 'bin.dat', status: 'M', insertions: null, deletions: null },
          ],
          tool_artifacts: ['.flowgate/run.log', 'tmp/x'],
        },
      },
    })
    const set = await fetchFlowChangeSet('p', 'g', { base: 'release' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/groups/g/changes', { base: 'release' })
    expect(set).toEqual({
      groupId: 'g',
      branch: 'flow-1',
      commit: 'abc',
      baseBranch: 'v0.2',
      changes: [
        { path: 'src/a.ts', status: 'M', insertions: 3, deletions: 1 },
        { path: 'bin.dat', status: 'M', insertions: null, deletions: null },
      ],
      floor: { workBaseRef: 'v0.2', scopeBaseSha: 'mb1', workBaseSha: 'wb1', workBaseSyncSha: null, workBaseState: 'verified' },
      toolArtifactCount: 2,
    })
    expect(JSON.stringify(set)).not.toContain('run.log')
  })

  it('a server without the floor leaves floor, counts and artifacts out (the 0024 shape)', async () => {
    getRequest.mockResolvedValue({ data: { data: { group_id: 'g', branch: 'flow-1', changes: [{ path: 'a', status: 'M' }] } } })
    const set = await fetchFlowChangeSet('p', 'g')
    expect(Object.keys(set).sort()).toEqual(['baseBranch', 'branch', 'changes', 'commit', 'groupId'])
    expect(Object.keys(set.changes[0]).sort()).toEqual(['path', 'status'])
  })

  it('a floor field alone builds the floor; a non-number count is null', async () => {
    getRequest.mockResolvedValue({
      data: { data: { group_id: 'g', branch: 'flow-1', work_base_ref: ' v0.2 ', changes: [{ path: 'a', status: 'M', insertions: '3' }] } },
    })
    const set = await fetchFlowChangeSet('p', 'g')
    expect(set.floor).toEqual({ workBaseRef: 'v0.2', scopeBaseSha: null, workBaseSha: null, workBaseSyncSha: null, workBaseState: null })
    expect(set.changes[0]).toEqual({ path: 'a', status: 'M', insertions: null })
  })
})

describe('/changes and /diff keep the transport error as cause, message unchanged (C3)', () => {
  const failures: Array<[string, unknown]> = [
    ['floor 409 unverified', Object.assign(new Error('x'), { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'group_work_base_unverified' } } } })],
    ['floor 409 diverged', Object.assign(new Error('x'), { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'group_work_base_diverged' } } } })],
    ['404', Object.assign(new Error('x'), { isAxiosError: true, response: { status: 404 } })],
    ['403', Object.assign(new Error('x'), { isAxiosError: true, response: { status: 403 } })],
    ['network', Object.assign(new Error('Network Error'), { isAxiosError: true })],
  ]

  it.each(failures)('/changes: %s', async (_label, transport) => {
    getRequest.mockRejectedValue(transport)
    const error = await fetchFlowChangeSet('p', 'g').catch((e) => e)
    expect(error.message).toBe('Failed to load the Flow changes.')
    expect(error.cause).toBe(transport)
  })

  it.each(failures)('/diff: %s', async (_label, transport) => {
    getRequest.mockRejectedValue(transport)
    const error = await fetchFlowFileDiff('p', 'g', 'src/a.ts').catch((e) => e)
    expect(error.message).toBe('Failed to load the file diff.')
    expect(error.cause).toBe(transport)
  })

  it('the Git part reads the server code through the kept cause', async () => {
    const { gitErrorOf } = await import('@/features/git/api')
    getRequest.mockRejectedValue(failures[0][1])
    const error = await fetchFlowFileDiff('p', 'g', 'src/a.ts').catch((e) => e)
    expect(gitErrorOf(error)).toMatchObject({ code: 'group_work_base_unverified', status: 409 })
  })
})
