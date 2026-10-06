import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0024 T0005 — Flow file context wrappers added to features/project/api.ts.
const getRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest: vi.fn(), extractApiErrorMessage }))

import {
  fetchBaseFileDiff,
  fetchFlowAiActivity,
  fetchFlowChangeSet,
  fetchFlowFileDiff,
  fetchFlowFileTree,
  fetchMergeReviewFileDiff,
  fetchProjectGitStatus,
} from '@/features/project/api'

beforeEach(() => {
  getRequest.mockReset()
  extractApiErrorMessage.mockClear()
})

describe('fetchProjectGitStatus', () => {
  it('normalizes Base and slots; a slot is writable only when the server says so', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        status: {
          enabled: true,
          base_branch: ' v0.2 ',
          base_branch_configured: true,
          slots: [
            { group_id: 'p.m.0001', branch: 'flow-1', status: 'active', writable: true },
            { group_id: 'p.m.0002', branch: null, status: 'active' },
          ],
        },
      },
    })
    const status = await fetchProjectGitStatus('p')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/status')
    expect(status).toEqual({
      enabled: true,
      baseBranch: 'v0.2',
      slots: [
        { groupId: 'p.m.0001', branch: 'flow-1', status: 'active', writable: true },
        { groupId: 'p.m.0002', branch: null, status: 'active', writable: false },
      ],
      // flowgate.v02.0033 additive fields: empty when the server sends none.
      pending: [],
      baseDirty: { files: [], mergeInProgress: false, aiRun: false },
      baseUntracked: { files: [], truncated: false, total: null },
    })
  })

  // flowgate.v02.0033 — what the Designer Git part reads from the same status answer.
  it('normalizes pending rows, Base dirty and untracked without the project default action', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        status: {
          enabled: true,
          base_branch: 'v0.2',
          base_branch_configured: true,
          slots: [],
          pending: [
            {
              group_id: 'p.m.0001',
              status: 'conflict',
              default_action: 'merge',
              merge_id: 7,
              ac_doc_id: 'p.m.0001.0009-AC',
              review_state: 'resolved_pending_review',
              reconciliation_kind: null,
              final_approval_bound: true,
            },
            { group_id: 'p.m.0002', status: 'waiting', default_action: 'merge', merge_id: null, ac_doc_id: null },
          ],
          base_dirty: { dirty: true, files: ['a\\b.ts'], merge_in_progress: { merge_id: 7 }, ai_run: null },
          base_untracked: { count: 200, files: ['n.md'], truncated: true },
        },
      },
    })
    const status = await fetchProjectGitStatus('p')
    expect(status?.pending).toEqual([
      {
        groupId: 'p.m.0001',
        status: 'conflict',
        mergeId: 7,
        acDocId: 'p.m.0001.0009-AC',
        reviewState: 'resolved_pending_review',
        finalApprovalBound: true,
        plan: null,
      },
      { groupId: 'p.m.0002', status: 'waiting', mergeId: null, acDocId: null, reviewState: null, finalApprovalBound: false, plan: null },
    ])
    expect(JSON.stringify(status)).not.toContain('default')
    expect(status?.baseDirty).toEqual({ files: ['a/b.ts'], mergeInProgress: true, aiRun: false })
    // `count` is the capped list length and is not carried; no S2 total = null (stage 1).
    expect(status?.baseUntracked).toEqual({ files: ['n.md'], truncated: true, total: null })
  })

  it('tells an S1 row without a plan apart from a server without S1, and reads the S2 total', async () => {
    getRequest.mockResolvedValue({
      data: {
        status: {
          enabled: true,
          slots: [],
          pending: [
            { group_id: 'a', status: 'waiting', planned_action: null, planned_target_branch: null },
            { group_id: 'b', status: 'waiting', planned_action: 'merge', planned_target_branch: ' main ' },
          ],
          base_untracked: { files: [], truncated: true, total: 250 },
        },
      },
    })
    const status = await fetchProjectGitStatus('p')
    expect(status?.pending.map((row) => row.plan)).toEqual([
      { action: null, targetBranch: null },
      { action: 'merge', targetBranch: 'main' },
    ])
    expect(status?.baseUntracked.total).toBe(250)
  })

  it('keeps the transport error as cause so a 403 can be told apart', async () => {
    const transport = Object.assign(new Error('x'), { response: { status: 403 } })
    getRequest.mockRejectedValueOnce(transport)
    const error = await fetchProjectGitStatus('p').catch((e) => e)
    expect(error.message).toBe('Failed to load the project Git status.')
    expect(error.cause).toBe(transport)
  })

  it.each([null, undefined, '', '  '])('reports an unconfigured Base (%j) as null, never a default branch', async (base) => {
    getRequest.mockResolvedValue({ data: { status: { enabled: false, base_branch: base, slots: [] } } })
    expect((await fetchProjectGitStatus('p'))?.baseBranch).toBeNull()
  })

  // 0024 TR0006: server project_git_status answers an unset/blank stored Base with its legacy
  // "main" fallback and base_branch_configured=false. That "main" is not the Project Base.
  it.each([
    ['the server main fallback', { base_branch: 'main', base_branch_configured: false }],
    ['a server that does not say', { base_branch: 'main' }],
    ['a null flag', { base_branch: 'v0.2', base_branch_configured: null }],
  ])('does not trust base_branch from %s', async (_label, fields) => {
    getRequest.mockResolvedValue({ data: { ok: true, status: { enabled: true, ...fields, slots: [] } } })
    expect((await fetchProjectGitStatus('p'))?.baseBranch).toBeNull()
  })

  it('returns null without a status payload and normalizes errors', async () => {
    getRequest.mockResolvedValueOnce({ data: { ok: true } })
    await expect(fetchProjectGitStatus('p')).resolves.toBeNull()
    getRequest.mockRejectedValueOnce(new Error('down'))
    await expect(fetchProjectGitStatus('p')).rejects.toThrow('Failed to load the project Git status.')
  })
})

describe('fetchFlowFileTree', () => {
  const payload = {
    data: {
      data: {
        branch: 'flow-1',
        commit: 'abc',
        nodes: [
          { id: '1', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src', permissions: ['read'] },
          { id: '2', parent_id: '1', type: 'file', name: 'a.ts', label: '', path: 'src\\a.ts', permissions: ['read', 'write'] },
          { id: '3', parent_id: null, type: 'file', name: 'secret', label: 'secret', path: 'secret', permissions: [] },
        ],
      },
    },
  }

  it('reads the group tree, keeps readable nodes only and maps to the camelCase model', async () => {
    getRequest.mockResolvedValue(payload)
    const tree = await fetchFlowFileTree('p', 'p.m.0001')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/groups/p.m.0001/tree')
    expect(tree).toEqual({
      branch: 'flow-1',
      commit: 'abc',
      nodes: [
        { id: '1', parentId: null, type: 'folder', name: 'src', label: 'src', path: 'src', permissions: ['read'] },
        { id: '2', parentId: '1', type: 'file', name: 'a.ts', label: 'a.ts', path: 'src/a.ts', permissions: ['read', 'write'] },
      ],
    })
  })

  it('retries once, then reports a normalized error', async () => {
    getRequest.mockRejectedValueOnce(new Error('busy')).mockResolvedValueOnce(payload)
    await expect(fetchFlowFileTree('p', 'g')).resolves.toMatchObject({ commit: 'abc' })
    expect(getRequest).toHaveBeenCalledTimes(2)

    getRequest.mockReset()
    getRequest.mockRejectedValue(new Error('down'))
    await expect(fetchFlowFileTree('p', 'g')).rejects.toThrow('Failed to load the Flow file tree.')
    expect(getRequest).toHaveBeenCalledTimes(2)
  })
})

describe('fetchFlowChangeSet', () => {
  const answer = (base: string | null) => ({
    data: { data: { group_id: 'g', branch: 'flow-1', commit: 'abc', base_branch: base, changes: [{ path: 'src\\a.ts', status: 'M' }, { path: 'n.md', status: '?' }] } },
  })

  it('sends the requested Base as the `base` query and reports the Base the server used', async () => {
    getRequest.mockResolvedValue(answer('main'))
    const set = await fetchFlowChangeSet('p', 'g', { base: 'main' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/groups/g/changes', { base: 'main' })
    expect(set).toEqual({
      groupId: 'g',
      branch: 'flow-1',
      commit: 'abc',
      baseBranch: 'main',
      changes: [{ path: 'src/a.ts', status: 'M' }, { path: 'n.md', status: '?' }],
    })
  })

  it('sends no base query when none is given and keeps an unnamed Base as null', async () => {
    getRequest.mockResolvedValue(answer(null))
    const set = await fetchFlowChangeSet('p', 'g')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/groups/g/changes', {})
    expect(set.baseBranch).toBeNull()
  })

  it('normalizes errors', async () => {
    getRequest.mockRejectedValue(new Error('down'))
    await expect(fetchFlowChangeSet('p', 'g', { base: 'v0.2' })).rejects.toThrow('Failed to load the Flow changes.')
  })
})

describe('fetchFlowFileDiff', () => {
  it('passes path, base and ref so the diff shares the change list merge-base', async () => {
    const side = { exists: true, binary: false, truncated: false, size: 1, content: 'x' }
    getRequest.mockResolvedValue({
      data: { data: { group_id: 'g', branch: 'flow-1', commit: 'abc', base_branch: 'v0.2', merge_base: 'mb', path: 'src/a.ts', status: 'M', old: side, new: side } },
    })
    const diff = await fetchFlowFileDiff('p', 'g', 'src/a.ts', { base: 'v0.2', ref: 'abc' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/groups/g/diff', { path: 'src/a.ts', base: 'v0.2', ref: 'abc' })
    expect(diff).toMatchObject({ groupId: 'g', baseBranch: 'v0.2', mergeBase: 'mb', status: 'M', old: side, new: side })

    await fetchFlowFileDiff('p', 'g', 'src/a.ts')
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/git/groups/g/diff', { path: 'src/a.ts' })
  })
})

describe('fetchFlowAiActivity', () => {
  it('is active only for a live run', async () => {
    const idle = { active: false, runId: null, status: null, actionScope: null }
    getRequest.mockResolvedValueOnce({ data: { ok: true, active: true, status: 'running', run_id: 'r1', action_scope: 'resolve_conflict' } })
    await expect(fetchFlowAiActivity('g')).resolves.toEqual({ active: true, runId: 'r1', status: 'running', actionScope: 'resolve_conflict' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: 'g' })
    getRequest.mockResolvedValueOnce({ data: { ok: true, active: true, status: 'finished', run_id: 'r1' } })
    await expect(fetchFlowAiActivity('g')).resolves.toEqual(idle)
    getRequest.mockResolvedValueOnce({ data: { ok: true, active: false } })
    await expect(fetchFlowAiActivity('g')).resolves.toEqual(idle)
  })

  it('normalizes errors', async () => {
    getRequest.mockRejectedValue(new Error('down'))
    await expect(fetchFlowAiActivity('g')).rejects.toThrow('Failed to load the Flow AI activity.')
  })
})

// flowgate.v02.0033 — the Designer Git part's file reads.
describe('fetchBaseFileDiff / fetchMergeReviewFileDiff', () => {
  const side = { exists: true, binary: false, truncated: false, size: 3, content: 'abc' }

  it('reads one Base checkout file with the server status letter', async () => {
    getRequest.mockResolvedValue({
      data: { ok: true, data: { group_id: null, branch: 'v0.2', base_branch: 'v0.2', commit: 'c1', path: 'src\\a.ts', status: 'A', old: { ...side, exists: false }, new: side } },
    })
    await expect(fetchBaseFileDiff('p', 'src/a.ts')).resolves.toEqual({
      branch: 'v0.2',
      commit: 'c1',
      path: 'src/a.ts',
      status: 'A',
      old: { ...side, exists: false },
      new: side,
    })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/git/diff', { path: 'src/a.ts' })
  })

  it('reads one file of the frozen review candidate', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, data: { group_id: 'p.m.0001', merge_id: 7, path: 'a.ts', status: 'M', old: side, new: side } } })
    await expect(fetchMergeReviewFileDiff('p.m.0001', 7, 'a.ts')).resolves.toEqual({
      groupId: 'p.m.0001',
      mergeId: 7,
      path: 'a.ts',
      status: 'M',
      old: side,
      new: side,
    })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/groups/p.m.0001/git/merge/7/review-diff', { path: 'a.ts' })
  })

  it('normalizes errors and keeps the cause (a hidden path is a 404)', async () => {
    const transport = Object.assign(new Error('x'), { response: { status: 404 } })
    getRequest.mockRejectedValue(transport)
    const error = await fetchBaseFileDiff('p', '.env').catch((e) => e)
    expect(error.message).toBe('Failed to load the file diff.')
    expect(error.cause).toBe(transport)
  })
})
