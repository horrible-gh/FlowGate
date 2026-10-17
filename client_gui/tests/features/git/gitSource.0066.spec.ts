import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0066 T#5 — the Group worktree boundary on GitSource (0066 D#2 §2-3, §5-1, C4):
// the HTTP source fills the optional `worktree` with the api.ts "Group worktree" functions, and a
// source without it (every 0033 / 0065 test source) still is a GitSource and makes no worktree
// request.
const getRequest = vi.hoisted(() => vi.fn(async () => ({ data: {} })))
const postRequest = vi.hoisted(() => vi.fn(async () => ({ data: {} })))
const putRequest = vi.hoisted(() => vi.fn(async () => ({ data: {} })))

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest,
  postRequest,
  putRequest,
}))

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { useProjectStore } from '@/features/project'
import { DesignerGitPanel, type GitSource, type GitWorktreeSource } from '@/features/git'
import * as api from '@/features/git/api'
import { createHttpGitSource } from '@/features/git/gitSource'
import type { WorktreeFile, WorktreeStatus } from '@/features/git/types'
import { G, P, createFakeGitSource } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus } from './fakeWorktreeSource'

registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockClear()
  postRequest.mockClear()
  putRequest.mockClear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

const WORKTREE_URL = /\/git\/worktree\/|\/git\/manual-merge|\/git\/merge-result|\/git\/fetch-refs/

describe('createHttpGitSource().worktree', () => {
  it('is the api.ts Group worktree section, member for member', () => {
    const worktree = createHttpGitSource().worktree as GitWorktreeSource
    expect(worktree).toBeTruthy()
    expect(worktree.loadStatus).toBe(api.fetchWorktreeStatus)
    expect(worktree.stage).toBe(api.stageWorktree)
    expect(worktree.unstage).toBe(api.unstageWorktree)
    expect(worktree.commit).toBe(api.commitWorktree)
    expect(worktree.revert).toBe(api.revertWorktree)
    expect(worktree.remove).toBe(api.removeWorktreeFiles)
    expect(worktree.suggestCommitMessage).toBe(api.suggestWorktreeCommitMessage)
    expect(worktree.loadManualMerge).toBe(api.fetchManualMerge)
    expect(worktree.manualMerge).toBe(api.requestManualMerge)
    expect(worktree.loadMergeResults).toBe(api.fetchMergeResults)
    expect(worktree.pushMergeResult).toBe(api.pushMergeResult)
    expect(worktree.fetchRefs).toBe(api.fetchRefs)
  })

  it('reads the target comparison list through the existing project /changes loader', async () => {
    const store = useProjectStore()
    const set = { groupId: G, branch: 'flow', commit: null, baseBranch: 'release', changes: [] }
    const loadFlowChanges = vi.spyOn(store, 'loadFlowChanges').mockResolvedValue(set)
    const worktree = createHttpGitSource().worktree as GitWorktreeSource
    await expect(worktree.loadTargetChanges(P, G, 'release')).resolves.toBe(set)
    expect(loadFlowChanges).toHaveBeenCalledWith(P, G, { base: 'release' })
  })

  // flowgate.v02.0067 T#4 — the Git modeless `Diff` tab detail reads through the source too.
  it('reads a group file diff through the existing project /diff loader; base only for a compared target', async () => {
    const store = useProjectStore()
    const diff = {
      groupId: G,
      branch: 'flow',
      commit: null,
      baseBranch: 'release',
      mergeBase: 'f1',
      path: 'src/a.ts',
      status: 'M' as const,
      old: { exists: true, binary: false, truncated: false, size: 1, content: 'a' },
      new: { exists: true, binary: false, truncated: false, size: 1, content: 'b' },
    }
    const loadFlowFileDiff = vi.spyOn(store, 'loadFlowFileDiff').mockResolvedValue(diff)
    const worktree = createHttpGitSource().worktree as GitWorktreeSource
    await expect(worktree.loadFileDiff(P, G, 'src/a.ts', 'release')).resolves.toBe(diff)
    await worktree.loadFileDiff(P, G, 'src/a.ts', null)
    expect(loadFlowFileDiff.mock.calls).toEqual([
      [P, G, 'src/a.ts', { base: 'release' }],
      [P, G, 'src/a.ts', {}],
    ])
  })

  it('keeps every existing member of the HTTP source', () => {
    const source = createHttpGitSource()
    expect(source.finalize).toBe(api.finalizeGroup)
    expect(source.push).toBe(api.pushBranch)
    expect(source.baseCommit).toBe(api.baseCommit)
    expect(source.baseRevert).toBe(api.baseRevert)
    expect(source.baseRemove).toBe(api.baseRemove)
    expect(source.baseCommitAll).toBeUndefined()
    expect(source.baseRevertAll).toBeUndefined()
  })
})

describe('a GitSource without `worktree` (C4)', () => {
  it('the 0033 / 0065 test source has none and still is a GitSource', () => {
    const { source } = createFakeGitSource()
    const asSource: GitSource = source
    expect(asSource.worktree).toBeUndefined()
  })

  it('the mounted panel makes no Group worktree request and shows no worktree area', async () => {
    const fake = createFakeGitSource()
    wrapper = mount(DesignerGitPanel, {
      props: { projectId: P, flow: { groupId: G, name: 'flow' }, source: fake.source },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(fake.source.loadStatus).toHaveBeenCalled()
    const urls = [...getRequest.mock.calls, ...postRequest.mock.calls].map((call) => String((call as unknown[])[0]))
    expect(urls.filter((url) => WORKTREE_URL.test(url))).toEqual([])
    expect(document.body.querySelector('[data-test^="git-worktree"]')).toBeNull()
  })
})

describe('fakeWorktreeSource (the T#6 / T#7 test double)', () => {
  // D#2 §3-2-3: the worktree session reloads E8 and an open Diff when one of these changed.
  const reloadsMerge = (before: WorktreeStatus, after: WorktreeStatus | null) =>
    !!after && (before.head !== after.head || before.worktreeToken !== after.worktreeToken)
  const newFile = (path: string, overrides: Partial<WorktreeFile> = {}) =>
    worktreeFile(path, { inHead: false, unstaged: null, untracked: true, canRevert: false, canRemove: true, ...overrides })
  const codeOf = async (call: Promise<unknown>) => {
    try {
      await call
    } catch (error) {
      return api.worktreeErrorOf(error).code
    }
    return 'resolved'
  }

  it('satisfies GitWorktreeSource and answers with models that follow its own state', async () => {
    const fake = createFakeWorktreeSource({ status: worktreeStatus({ files: [worktreeFile('a.ts'), newFile('b.ts')] }) })
    const worktree: GitWorktreeSource = fake.source
    const staged = await worktree.stage(G, { paths: ['a.ts'] }, 'wt-1')
    expect(staged.applied).toEqual(['a.ts'])
    expect(staged.status?.counts.staged).toBe(1)
    const committed = await worktree.commit(G, ' feat: a ', staged.status?.indexToken ?? null)
    expect(committed).toMatchObject({ subject: 'feat: a', committedPaths: ['a.ts'], committedCount: 1 })
    expect(committed.status?.files.map((f) => f.path)).toEqual(['b.ts'])
    const removed = await worktree.remove(G, ['b.ts'], committed.status?.worktreeToken ?? null)
    expect(removed.status?.counts.changed).toBe(0)
    const { source } = createFakeGitSource({}, { worktree })
    expect(source.worktree).toBe(worktree)
  })

  it('revert / remove give a new worktreeToken (the E8 reload condition); indexToken only when the index changed', async () => {
    const fake = createFakeWorktreeSource()
    const before = await fake.source.loadStatus()
    const reverted = await fake.source.revert(G, ['src/a.ts'], before.worktreeToken)
    expect(reverted.results).toEqual([{ path: 'src/a.ts', result: 'reverted' }])
    expect(reverted.status?.counts.changed).toBe(0)
    expect(reverted.status).toMatchObject({ head: 'h1', indexToken: 'ix-1' })
    expect(reverted.status?.worktreeToken).not.toBe(before.worktreeToken)
    expect(reloadsMerge(before, reverted.status)).toBe(true)

    const withNew = createFakeWorktreeSource({ status: worktreeStatus({ files: [newFile('n.ts')] }) })
    const removed = await withNew.source.remove(G, ['n.ts'], 'wt-1')
    expect(removed.results).toEqual([{ path: 'n.ts', result: 'removed' }])
    expect(removed.status).toMatchObject({ head: 'h1', indexToken: 'ix-1', files: [] })
    expect(removed.status?.counts.changed).toBe(0)
    expect(removed.status?.worktreeToken).not.toBe('wt-1')
    expect(reloadsMerge(before, removed.status)).toBe(true)

    // Reverting a staged file puts the index back to HEAD too.
    const withStage = createFakeWorktreeSource({
      status: worktreeStatus({ files: [worktreeFile('s.ts', { staged: 'M', unstaged: null, canUnstage: true })] }),
    })
    const stagedRevert = await withStage.source.revert(G, ['s.ts'], null)
    expect(stagedRevert.status?.indexToken).not.toBe('ix-1')
    expect(stagedRevert.status?.worktreeToken).not.toBe('wt-1')
    expect(stagedRevert.status?.counts).toMatchObject({ changed: 0, staged: 0 })

    // Nothing changed, nothing to reload.
    const noop = await fake.source.revert(G, ['other.ts'], null)
    expect(noop.results).toEqual([{ path: 'other.ts', result: 'not_changed' }])
    expect(reloadsMerge(reverted.status as WorktreeStatus, noop.status)).toBe(false)
    expect(noop.status?.indexToken).toBe('ix-1')
  })

  it('a commit is a new snapshot: HEAD, indexToken and worktreeToken all change', async () => {
    const fake = createFakeWorktreeSource({
      status: worktreeStatus({ files: [worktreeFile('s.ts', { staged: 'M', unstaged: null, canUnstage: true })] }),
    })
    const before = await fake.source.loadStatus()
    const result = await fake.source.commit(G, 'feat: s', before.indexToken)
    expect(result.status?.head).toBe(result.commitSha)
    expect(result.commit).toBe(result.commitSha?.slice(0, 7))
    expect(result.status?.head).not.toBe(before.head)
    expect(result.status?.indexToken).not.toBe(before.indexToken)
    expect(result.status?.worktreeToken).not.toBe(before.worktreeToken)
    expect(result.status?.committed).toEqual({ commitCount: 1, changedFileCount: 1 })
    expect(reloadsMerge(before, result.status)).toBe(true)
    expect(await fake.source.loadStatus()).toEqual(result.status)
  })

  it('a commit takes the Stage only: the unstaged part of a partly staged file stays listed (D#1 §3-4(7))', async () => {
    const fake = createFakeWorktreeSource({
      status: worktreeStatus({
        files: [
          worktreeFile('partial.ts', { staged: 'M', unstaged: 'M', canUnstage: true }),
          newFile('added.ts', { staged: 'A', unstaged: 'M', untracked: false, canUnstage: true }),
          worktreeFile('left.ts'),
        ],
      }),
    })
    const result = await fake.source.commit(G, 'feat: partial', 'ix-1')
    expect(result.committedPaths).toEqual(['partial.ts', 'added.ts'])
    expect(result.status?.files).toEqual([
      worktreeFile('partial.ts'),
      // Now in HEAD: revert, not remove.
      worktreeFile('added.ts'),
      worktreeFile('left.ts'),
    ])
    expect(result.status?.counts).toMatchObject({ changed: 3, staged: 0, unstaged: 3, untracked: 0 })
    // A staged deletion leaves HEAD; an untracked copy of it is a new file again.
    const deleted = createFakeWorktreeSource({
      status: worktreeStatus({
        files: [
          worktreeFile('gone.ts', { staged: 'D', unstaged: null, canUnstage: true }),
          worktreeFile('back.ts', { staged: 'D', unstaged: null, untracked: true, canUnstage: true }),
        ],
      }),
    })
    const afterDelete = await deleted.source.commit(G, 'chore: delete', null)
    expect(afterDelete.status?.files).toEqual([newFile('back.ts')])
    expect(afterDelete.status?.counts).toMatchObject({ changed: 1, staged: 0, unstaged: 0, untracked: 1 })
  })

  it('staging a deleted file stages the deletion, and unstaging gives the unstaged deletion back', async () => {
    const fake = createFakeWorktreeSource({ status: worktreeStatus({ files: [worktreeFile('gone.ts', { unstaged: 'D' })] }) })
    const staged = await fake.source.stage(G, { paths: ['gone.ts'] }, 'wt-1')
    expect(staged.status?.files).toEqual([worktreeFile('gone.ts', { staged: 'D', unstaged: null, canUnstage: true })])
    expect(staged.status?.counts).toMatchObject({ changed: 1, staged: 1, unstaged: 0, untracked: 0 })
    expect(staged.status?.indexToken).not.toBe('ix-1')
    expect(staged.status?.worktreeToken).not.toBe('wt-1')
    const released = await fake.source.unstage(G, { paths: ['gone.ts'] }, staged.status?.worktreeToken ?? null)
    expect(released.status?.files).toEqual([worktreeFile('gone.ts', { unstaged: 'D' })])
    expect(released.status?.counts).toMatchObject({ changed: 1, staged: 0, unstaged: 1, untracked: 0 })
  })

  it('a new file goes back to untracked when its Stage is released', async () => {
    const fake = createFakeWorktreeSource({ status: worktreeStatus({ files: [newFile('n.ts')] }) })
    const staged = await fake.source.stage(G, { all: true }, null)
    expect(staged.applied).toEqual(['n.ts'])
    expect(staged.status?.files).toEqual([newFile('n.ts', { staged: 'A', untracked: false, canUnstage: true })])
    expect(staged.status?.counts).toMatchObject({ changed: 1, staged: 1, unstaged: 0, untracked: 0 })
    const released = await fake.source.unstage(G, { all: true }, null)
    expect(released.applied).toEqual(['n.ts'])
    expect(released.status?.files).toEqual([newFile('n.ts')])
    expect(released.status?.counts).toMatchObject({ changed: 1, staged: 0, unstaged: 0, untracked: 1 })
    expect(released.status?.indexToken).not.toBe(staged.status?.indexToken)
  })

  it('a partly staged file keeps its working change across Stage release; a full unstage releases a hidden Stage', async () => {
    const fake = createFakeWorktreeSource({
      status: worktreeStatus({ files: [worktreeFile('p.ts', { staged: 'M', unstaged: 'M', canUnstage: true })] }),
    })
    const released = await fake.source.unstage(G, { paths: ['p.ts'] }, null)
    expect(released.status?.files).toEqual([worktreeFile('p.ts')])

    const hiddenOnly = worktreeStatus({
      files: [],
      counts: { changed: 0, staged: 1, unstaged: 0, untracked: 0, excludedNewUntracked: 0, hidden: 1, managedDirty: 0, unsupported: 0 },
    })
    const hidden = createFakeWorktreeSource({ status: hiddenOnly })
    expect(await codeOf(hidden.source.commit(G, 'feat: x', null))).toBe('staged_protected_paths')
    const all = await hidden.source.unstage(G, { all: true }, null)
    expect(all).toMatchObject({ applied: [], appliedHiddenCount: 1 })
    expect(all.status?.counts).toMatchObject({ changed: 0, staged: 0, hidden: 1 })
    expect(all.status?.indexToken).not.toBe('ix-1')
  })

  it('refuses what the server refuses and then keeps its state', async () => {
    const fake = createFakeWorktreeSource({ status: worktreeStatus({ files: [worktreeFile('a.ts'), newFile('n.ts')] }) })
    const before = await fake.source.loadStatus()
    expect(await codeOf(fake.source.stage(G, { paths: ['a.ts'] }, 'wt-old'))).toBe('worktree_changed')
    expect(await codeOf(fake.source.commit(G, 'feat: x', 'ix-old'))).toBe('worktree_changed')
    expect(await codeOf(fake.source.commit(G, 'feat: x', null))).toBe('nothing_staged')
    expect(await codeOf(fake.source.commit(G, '   ', null))).toBe('commit_message_required')
    expect(await codeOf(fake.source.unstage(G, { paths: ['a.ts'] }, null))).toBe('path_protected')
    expect(await codeOf(fake.source.stage(G, { paths: ['missing.ts'] }, null))).toBe('path_protected')
    expect(await codeOf(fake.source.revert(G, ['n.ts'], null))).toBe('new_path_use_remove')
    expect(await codeOf(fake.source.remove(G, ['a.ts'], null))).toBe('existing_path_use_revert')
    expect(await fake.source.loadStatus()).toEqual(before)
  })
})
