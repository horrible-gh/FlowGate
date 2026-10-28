/** T#3 D §6.2-8/9/10: deliberately different project, display, merge and planned targets.
 * Source fixtures answer reads; actual API adapters send writes to a mocked transport. */
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession, type GitWorktreeSession } from '@/features/git/gitWorktreeSession'
import { createHttpGitSource } from '@/features/git/gitSource'
import { checkApprove } from '@/features/document/api/check'
import { G, P, createFakeGitSource, gitError, manualSleep, resolvingRun, row, status } from './fakeGitSource'
import { createFakeWorktreeSource, manualMergeView, mergeResultRow, mergeResults, worktreeStatus } from './fakeWorktreeSource'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({
  ...(await original<typeof import('@shared/api')>()), getRequest: get, postRequest: post,
}))
const FLOW = { groupId: G, name: 'A' }
const DISPLAY = 'feature/v0.2.0'
const DEFAULT = 'release/e8'
const CHOSEN = 'release/chosen'
const PLAN = 'release/finalize'
let sessions: Array<{ git: GitSession; wt: GitWorktreeSession }> = []
beforeEach(() => {
  setActivePinia(createPinia())
  get.mockReset().mockImplementation(async (url: string) => {
    if (!url.endsWith('/manual-merge')) throw new Error(`unexpected GET ${url}`)
    return { data: { ok: true, result: { can_merge: true, default_target: DEFAULT,
      targets: [DEFAULT, CHOSEN].map(name => ({ name, kind: 'local', eligible: true, blocked_reason: null, is_work_base: name === DEFAULT })),
      ahead_of_target: 1, merge_needed: true,
    } } }
  })
  post.mockReset().mockImplementation(async (url: string) => {
    if (url.endsWith('/review_transitions/approve')) return { data: { approval: { approved: true } } }
    return { data: { ok: true, result: { status: 'merged', target_branch: CHOSEN, merge_id: 11, merge_commit: 'a'.repeat(40) } } }
  })
})
afterEach(() => { sessions.forEach(t => { t.wt.dispose(); t.git.dispose() }); sessions = [] })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
function setup(pending: ReturnType<typeof row>[] = []) {
  const http = createHttpGitSource()
  const wt = createFakeWorktreeSource({
    workBase: { workBaseRef: DISPLAY, effectiveWorkBaseRef: DISPLAY },
    merge: manualMergeView({ defaultTarget: DEFAULT, targets: [DEFAULT, CHOSEN].map(name => ({
      name, kind: 'local', eligible: true, blockedReason: null, isWorkBase: name === DEFAULT,
    })) }),
    results: mergeResults({ results: [mergeResultRow({ target: { targetBranch: CHOSEN, isProjectBase: false } })] }),
  }, { manualMerge: http.worktree!.manualMerge, pushMergeResult: http.worktree!.pushMergeResult })
  wt.source.loadManualMerge.mockImplementation(http.worktree!.loadManualMerge)
  const fake = createFakeGitSource({ status: status({ baseBranch: 'main', pending }) }, {
    worktree: wt.source, finalize: http.finalize, push: http.push,
  })
  const clock = manualSleep()
  const git = createGitSession({ source: fake.source, sleep: clock.sleep })
  const session = createGitWorktreeSession({ git, source: fake.source })
  sessions.push({ git, wt: session })
  return { wt, fake, git, session, clock }
}
type T = ReturnType<typeof setup>
async function open(t: T) { t.git.setContext(P, FLOW); await flushPromises() }
async function changeDisplay(t: T) {
  t.wt.db.workBase = { effectiveWorkBaseRef: 'display/changed' }
  await t.session.refreshWorkBase()
  expect(t.session.state.workBase).toBe('display/changed')
  expect(t.git.state.status?.baseBranch).toBe('main')
}

describe('D §6.2-9/10 — target contracts across a display reread', () => {
  it.each([false, true])('E8 default and user choice survive display reread; E9 sends the chosen target with push=%s', async push => {
    const t = setup(); await open(t)
    expect(t.session.state.workBase).toBe(DISPLAY)
    expect(t.session.state.merge?.defaultTarget).toBe(DEFAULT)
    expect(t.session.state.mergeTarget).toBe(DEFAULT)
    expect(get).toHaveBeenCalledWith(`/api/v1/groups/${G}/git/manual-merge`)
    t.session.setMergeTarget(CHOSEN); await flushPromises()
    expect(get).toHaveBeenLastCalledWith(`/api/v1/groups/${G}/git/manual-merge`, { target: CHOSEN })
    t.session.setPushAfterMerge(push)
    const confirmation = t.session.mergeSnapshot()!
    expect(confirmation).toMatchObject({ target: CHOSEN, push })
    await changeDisplay(t)
    expect(t.session.state.mergeTarget).toBe(CHOSEN)
    expect(t.session.mergeSnapshot()).toEqual(confirmation)
    await t.session.merge(confirmation); await flushPromises()
    expect(post).toHaveBeenCalledWith(`/api/v1/groups/${G}/git/manual-merge`, { target_branch: CHOSEN, push })
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('Diff, Fetch and recorded-result Push keep their own targets and add no display reads', async () => {
    const t = setup(); await open(t)
    t.session.setMergeTarget(CHOSEN); await flushPromises()
    await changeDisplay(t)
    const before = t.wt.source.loadWorkBase.mock.calls.length
    t.session.toggleApply(); t.session.toggleInfo('diff'); await flushPromises()
    expect(t.wt.source.loadTargetChanges).toHaveBeenLastCalledWith(P, G, CHOSEN)
    await t.session.fetchRemote(); await flushPromises()
    expect(t.wt.source.fetchRefs).toHaveBeenCalledWith(P, G)
    expect(t.session.state.mergeTarget).toBe(CHOSEN)
    const confirmation = t.session.pushSnapshot()!
    expect(confirmation).toMatchObject({ target: CHOSEN, mergeId: 11 })
    await t.session.pushResult(confirmation); await flushPromises()
    expect(post).toHaveBeenCalledWith(`/api/v1/groups/${G}/git/merge-result/push`, { merge_id: 11, merge_commit: 'a'.repeat(40) })
    expect(t.wt.source.loadWorkBase).toHaveBeenCalledTimes(before)
  })

  it.each(['merge', 'merge_only', 'push'] as const)('planned finalize action %s preserves its explicit target contract', async action => {
    const t = setup([row({ status: 'waiting', mergeId: null, plan: { action, targetBranch: PLAN } })])
    await open(t); await changeDisplay(t)
    expect(t.git.holdView.value?.targetBranch).toBe(PLAN)
    await t.git.applyNow(); await flushPromises()
    expect(post).toHaveBeenCalledWith(`/api/v1/groups/${G}/git/finalize`, action === 'push'
      ? { action } : { action, git_target_branch: PLAN })
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('project Push transport retains main before and after the display base changes', async () => {
    const t = setup(); await open(t)
    post.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    await expect(t.fake.source.push(P, 'main')).rejects.toBeDefined()
    await changeDisplay(t)
    await t.fake.source.push(P, 'main')
    expect(post.mock.calls).toEqual([
      [`/api/v1/projects/${P}/git/push`, { branch: 'main' }],
      [`/api/v1/projects/${P}/git/push`, { branch: 'main' }],
    ])
    expect(t.session.projectGit.value?.baseBranch).toBe('main')
  })

  it('a rejected finalize retries the stored planned target after a display change', async () => {
    const t = setup([row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: PLAN } })])
    await open(t)
    post.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    await t.git.applyNow(); await flushPromises()
    expect(t.git.state.pushFailure?.request).toMatchObject({ kind: 'finalize', targetBranch: PLAN })
    await changeDisplay(t)
    await t.git.retryApply(); await flushPromises()
    expect(post.mock.calls).toEqual(Array(2).fill([
      `/api/v1/groups/${G}/git/finalize`, { action: 'merge', git_target_branch: PLAN },
    ]))
  })

  it.each(['merge', 'merge_only', 'wait'] as const)('confirmation-card approval %s preserves the server-owned target payload', async choice => {
    const t = setup(); await open(t); await changeDisplay(t)
    const docId = `${G}.0012-AC`
    await checkApprove({ docId, choice })
    expect(post).toHaveBeenCalledWith('/api/v1/documents/review_transitions/approve', choice === 'wait'
      ? { doc_id: docId, comment: null } : { doc_id: docId, comment: null, git_action: choice })
    expect(post).toHaveBeenCalledTimes(1)
  })
})

describe('D §6.2-8/9 — polling and E1 write ordering', () => {
  it('actual project polling and changed E1/E8 fingerprints never trigger another display read', async () => {
    const t = setup([row()]); t.fake.db.ai = resolvingRun; await open(t)
    expect(t.clock.pending).toBeGreaterThan(0)
    const initial = t.fake.source.loadStatus.mock.calls.length
    const e1 = t.wt.source.loadStatus.mock.calls.length
    t.clock.tick(); await flushPromises()
    expect(t.fake.source.loadStatus.mock.calls.length).toBeGreaterThan(initial)
    t.fake.db.status = status({ baseBranch: 'main', pending: [row({ mergeId: 8 })] })
    t.clock.tick(); await flushPromises()
    expect(t.wt.source.loadStatus.mock.calls.length).toBeGreaterThan(e1)
    t.session.refresh(); await flushPromises()
    expect(t.wt.source.loadWorkBase).toHaveBeenCalledTimes(1)
    t.wt.source.loadWorkBase.mockRejectedValueOnce(gitError(404, 'not_found'))
    await t.session.refreshWorkBase()
    for (let n = 0; n < 5; n++) { t.clock.tick(); await flushPromises() }
    expect(t.wt.source.loadWorkBase).toHaveBeenCalledTimes(2)
    expect(t.session.state.workBaseLoad).toBe('error')
  })

  it('display reread during an E2 write leaves queuing and E1 status ordering intact', async () => {
    const t = setup(); await open(t)
    const old = deferred<ReturnType<typeof worktreeStatus>>()
    t.wt.source.loadStatus.mockImplementationOnce(() => old.promise)
    t.session.refresh(); await flushPromises()
    const stage = deferred<Awaited<ReturnType<typeof t.wt.source.stage>>>()
    t.wt.source.stage.mockImplementationOnce(() => stage.promise)
    const writing = t.session.stagePath('src/a.ts')
    expect(t.session.state.busy).toBe('stage')
    const e1 = t.wt.source.loadStatus.mock.calls.length
    t.session.refresh(); await flushPromises()
    await changeDisplay(t)
    expect(t.session.state.busy).toBe('stage')
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(e1)
    t.wt.db.status = worktreeStatus({ worktreeToken: 'wt-new', indexToken: 'ix-new' })
    stage.resolve({ applied: ['src/a.ts'], appliedHiddenCount: 0,
      skipped: { managed: 0, hidden: 0, excludedNew: 0, unsupported: 0 }, status: t.wt.db.status, statusError: null })
    await writing; await flushPromises()
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(e1 + 1)
    old.resolve(worktreeStatus({ worktreeToken: 'wt-stale' })); await flushPromises()
    expect(t.session.state.status?.worktreeToken).toBe('wt-new')
    expect(t.session.state.workBase).toBe('display/changed')
  })
})
