import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import type { ProjectGitStatus } from '@/features/project'
import type { GitCenterDiffRefresh, GitSource } from '@/features/git'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession, type GitWorktreeSession } from '@/features/git/gitWorktreeSession'
import type { GitWorktreeSource } from '@/features/git/gitSource'
import type { ManualMergeView, WorktreeStatus } from '@/features/git/types'
import { G, P, createFakeGitSource, gitError, manualSleep, networkError, status } from './fakeGitSource'
import { createFakeWorktreeSource, manualMergeView, noDetails, worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02.0066 T#6 — the Group worktree session (0066 D#2 §3-1, §3-2): gate, generations,
// the shared E1 sequence, reads held during a write, and what a new fingerprint reads again;
// late write answers of an older context and confirm tickets bound to their context.

const FLOW = { groupId: G, name: '인증 오류 조사' }
const G2 = 'p.m.0002'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let sessions: Array<{ git: GitSession; wt: GitWorktreeSession }> = []
afterEach(() => {
  for (const s of sessions) {
    s.wt.dispose()
    s.git.dispose()
  }
  sessions = []
})

function setup(
  init: {
    wt?: Parameters<typeof createFakeWorktreeSource>[0]
    wtExtra?: Partial<GitWorktreeSource>
    gitStatus?: ProjectGitStatus | null
    gitExtra?: Partial<GitSource>
  } = {},
) {
  const wt = createFakeWorktreeSource(init.wt ?? {}, init.wtExtra ?? {})
  const fake = createFakeGitSource(init.gitStatus === undefined ? {} : { status: init.gitStatus }, {
    worktree: wt.source,
    ...(init.gitExtra ?? {}),
  })
  const git = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
  const refreshes: GitCenterDiffRefresh[] = []
  const session = createGitWorktreeSession({ git, source: fake.source, onCenterDiffRefresh: (r) => refreshes.push(r) })
  sessions.push({ git, wt: session })
  return { wt, fake, git, session, refreshes }
}

const calls = (t: ReturnType<typeof setup>) => ({
  e1: t.wt.source.loadStatus.mock.calls.length,
  e8: t.wt.source.loadManualMerge.mock.calls.length,
  e10: t.wt.source.loadMergeResults.mock.calls.length,
  list: t.wt.source.loadTargetChanges.mock.calls.length,
})

async function open(t: ReturnType<typeof setup>, flow = FLOW) {
  t.git.setContext(P, flow)
  await flushPromises()
}

describe('gitWorktreeSession — gate and first reads (C5)', () => {
  it('(a) nothing is read before the first git/status answer; (d) after enabled=true, E1 / E8 / E10 once each', async () => {
    const answer = deferred<ProjectGitStatus | null>()
    const t = setup({ gitExtra: {} })
    t.fake.source.loadStatus.mockImplementationOnce(() => answer.promise)
    await open(t)
    expect(t.git.state.statusLoad).toBe('loading')
    expect(t.session.gateOpen.value).toBe(false)
    expect(calls(t)).toEqual({ e1: 0, e8: 0, e10: 0, list: 0 })
    answer.resolve(status())
    await flushPromises()
    expect(t.session.gateOpen.value).toBe(true)
    expect(calls(t)).toEqual({ e1: 1, e8: 1, e10: 1, list: 0 })
    expect(t.wt.source.loadManualMerge).toHaveBeenCalledWith(G, null)
    // The default target answer picks the selection; the area starts folded.
    expect(t.session.state.mergeTarget).toBe('v0.2')
    expect(t.session.state.status?.worktreeToken).toBe('wt-1')
    expect(t.session.state.filesOpen).toBe(false)
  })

  it('(b) Git off and (c) a failed git/status read the worktree never', async () => {
    const off = setup({ gitStatus: status({ enabled: false }) })
    await open(off)
    expect(off.session.gateOpen.value).toBe(false)
    expect(calls(off)).toEqual({ e1: 0, e8: 0, e10: 0, list: 0 })

    const failed = setup()
    failed.fake.source.loadStatus.mockRejectedValue(gitError(500, 'git_error'))
    await open(failed)
    expect(failed.git.state.statusLoad).toBe('error')
    expect(calls(failed)).toEqual({ e1: 0, e8: 0, e10: 0, list: 0 })
  })

  it('no active Flow, or a source without a worktree, reads nothing', async () => {
    const noFlow = setup()
    noFlow.git.setContext(P, null)
    await flushPromises()
    expect(calls(noFlow)).toEqual({ e1: 0, e8: 0, e10: 0, list: 0 })

    const fake = createFakeGitSource()
    const git = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
    const session = createGitWorktreeSession({ git, source: fake.source })
    sessions.push({ git, wt: session })
    git.setContext(P, FLOW)
    await flushPromises()
    expect(git.state.statusLoad).toBe('ready')
    expect(session.gateOpen.value).toBe(false)
  })

  it('(e) Git turned on by a later read (0065 connect) is when the first reads go out', async () => {
    const t = setup({ gitStatus: status({ enabled: false }) })
    await open(t)
    expect(calls(t).e1).toBe(0)
    t.fake.db.status = status()
    await t.git.refresh()
    await flushPromises()
    expect(calls(t)).toEqual({ e1: 1, e8: 1, e10: 1, list: 0 })
  })

  it('(f) a failed re-read closes the gate: server values go, a late E1 answer is dropped, typed text stays', async () => {
    const t = setup()
    await open(t)
    t.session.setMessage('feat: kept')
    t.session.toggleFiles()
    const late = deferred<WorktreeStatus>()
    t.wt.source.loadStatus.mockImplementationOnce(() => late.promise)
    t.session.refresh()
    await flushPromises()
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.git.refresh()
    await flushPromises()
    expect(t.session.gateOpen.value).toBe(false)
    expect(t.session.state.status).toBeNull()
    expect(t.session.state.merge).toBeNull()
    late.resolve(worktreeStatus({ worktreeToken: 'wt-late' }))
    await flushPromises()
    expect(t.session.state.status).toBeNull()
    expect(t.session.state.message).toBe('feat: kept')
    expect(t.session.state.filesOpen).toBe(true)
  })

  it('a network failure of an E1 re-read keeps the last answer and says so; other failures clear it', async () => {
    const t = setup()
    await open(t)
    t.wt.source.loadStatus.mockRejectedValueOnce(networkError())
    t.session.refresh()
    await flushPromises()
    expect(t.session.state.status?.worktreeToken).toBe('wt-1')
    expect(t.session.state.statusError).toBe('network')
    t.wt.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    t.session.refresh()
    await flushPromises()
    expect(t.session.state.status).toBeNull()
    expect(t.session.state.statusError).toBe('failed')
    t.session.refresh()
    await flushPromises()
    expect(t.session.state.status?.worktreeToken).toBe('wt-1')
    expect(t.session.state.statusError).toBeNull()
  })
})

describe('gitWorktreeSession — context switch (C23 a)', () => {
  it('a Flow switch drops late E1 / E8 / E10 answers and resets every screen state', async () => {
    const t = setup()
    await open(t)
    t.session.toggleFiles()
    t.session.toggleSearch()
    t.session.setSearch('a.ts')
    t.session.setSelected('src/a.ts', true)
    t.session.setMessage('feat: lost')
    const e1 = deferred<WorktreeStatus>()
    const e8 = deferred<ManualMergeView>()
    t.wt.source.loadStatus.mockImplementationOnce(() => e1.promise)
    t.wt.source.loadManualMerge.mockImplementationOnce(() => e8.promise)
    t.session.refresh()
    await flushPromises()
    await open(t, { groupId: G2, name: '다른 Flow' })
    expect(t.session.state.filesOpen).toBe(false)
    expect(t.session.state.searchOpen).toBe(false)
    expect(t.session.state.search).toBe('')
    expect(t.session.state.selected).toEqual([])
    expect(t.session.state.message).toBe('')
    expect(t.session.state.mergeTarget).toBe('v0.2')
    const after = t.session.state.status
    e1.resolve(worktreeStatus({ worktreeToken: 'wt-old-flow', groupId: G }))
    e8.resolve(manualMergeView({ aheadOfTarget: 99 }))
    await flushPromises()
    expect(t.session.state.status).toEqual(after)
    expect(t.session.state.merge?.aheadOfTarget).toBe(1)
    // The new Flow was read with its own group id.
    expect(t.wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G2)
  })
})

describe('gitWorktreeSession — answer order (C27)', () => {
  it('(a) a read sent before a write never covers the status the write returned', async () => {
    const t = setup()
    await open(t)
    const read = deferred<WorktreeStatus>()
    t.wt.source.loadStatus.mockImplementationOnce(() => read.promise)
    t.session.refresh()
    await flushPromises()
    await t.session.stagePath('src/a.ts')
    const afterWrite = t.session.state.status?.worktreeToken
    expect(afterWrite).not.toBe('wt-1')
    expect(t.session.state.status?.files[0].staged).toBe('M')
    read.resolve(worktreeStatus())
    await flushPromises()
    expect(t.session.state.status?.worktreeToken).toBe(afterWrite)
    expect(t.session.state.status?.files[0].staged).toBe('M')
  })

  it('(b) a refresh during a write is held and sent once after the write answered', async () => {
    const t = setup()
    await open(t)
    const write = deferred<void>()
    const real = t.wt.source.stage.getMockImplementation()!
    t.wt.source.stage.mockImplementationOnce(async (...args) => {
      const result = await real(...args)
      return write.promise.then(() => result)
    })
    const before = calls(t)
    const pending = t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.session.state.busy).toBe('stage')
    t.session.refresh()
    t.session.refresh()
    await flushPromises()
    expect(calls(t).e1).toBe(before.e1)
    write.resolve()
    await pending
    await flushPromises()
    expect(calls(t).e1).toBe(before.e1 + 1)
    expect(calls(t).e10).toBe(before.e10 + 1)
    // The fingerprint changed too: one E8, not two.
    expect(calls(t).e8).toBe(before.e8 + 1)
  })

  it('(c) an E8 sent before a write and answered after it is dropped; the new one applies', async () => {
    const t = setup()
    await open(t)
    const old = deferred<ManualMergeView>()
    t.wt.source.loadManualMerge.mockImplementationOnce(() => old.promise)
    t.session.refresh()
    await flushPromises()
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 3 })
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.session.state.merge?.aheadOfTarget).toBe(3)
    old.resolve(manualMergeView({ aheadOfTarget: 7 }))
    await flushPromises()
    expect(t.session.state.merge?.aheadOfTarget).toBe(3)
  })
})

describe('gitWorktreeSession — dependents of the status fingerprint (C10, C28)', () => {
  const blockedByUncommitted = () =>
    manualMergeView({
      canMerge: false,
      aheadOfTarget: 2,
      blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 1 }) }],
    })

  it('(a) reverting the last uncommitted file reads the merge judgment again, same target, no refresh', async () => {
    const t = setup({ wt: { merge: blockedByUncommitted() } })
    await open(t)
    expect(t.session.state.merge?.canMerge).toBe(false)
    const before = calls(t)
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 2 })
    await t.session.revert('src/a.ts')
    await flushPromises()
    expect(t.wt.source.revert).toHaveBeenCalledWith(G, ['src/a.ts'], 'wt-1')
    expect(t.session.state.status?.files).toEqual([])
    expect(calls(t).e8).toBe(before.e8 + 1)
    expect(t.wt.source.loadManualMerge.mock.calls.at(-1)).toEqual([G, 'v0.2'])
    expect(t.session.state.merge?.canMerge).toBe(true)
    // (c) the open Center Diff of this group reads again; (d) E10 is not read again.
    expect(t.refreshes).toEqual([{ groupId: G, closeCompare: false }])
    expect(calls(t).e10).toBe(before.e10)
    expect(calls(t).e1).toBe(before.e1)
  })

  it('(b) the same through removing a new file', async () => {
    const t = setup({
      wt: {
        status: worktreeStatus({
          files: [worktreeFile('n.ts', { inHead: false, unstaged: null, untracked: true, canRevert: false, canRemove: true })],
        }),
        merge: blockedByUncommitted(),
      },
    })
    await open(t)
    t.wt.db.merge = manualMergeView()
    await t.session.remove('n.ts')
    await flushPromises()
    expect(t.wt.source.remove).toHaveBeenCalledWith(G, ['n.ts'], 'wt-1')
    expect(t.session.state.merge?.canMerge).toBe(true)
    expect(t.refreshes).toHaveLength(1)
  })

  it('the judgment is void while it is read again ("확인 중"), the target list stays', async () => {
    const t = setup()
    await open(t)
    const next = deferred<ManualMergeView>()
    t.wt.source.loadManualMerge.mockImplementationOnce(() => next.promise)
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.session.state.merge).toBeNull()
    expect(t.session.state.mergeLoad).toBe('loading')
    expect(t.session.state.mergeTargets.map((x) => x.name)).toEqual(['v0.2', 'release'])
    next.resolve(manualMergeView())
    await flushPromises()
    expect(t.session.state.mergeLoad).toBe('ready')
  })

  it('(e) an E1 answer with the same fingerprint reads nothing else', async () => {
    const t = setup()
    await open(t)
    const before = calls(t)
    t.wt.source.stage.mockRejectedValueOnce(gitError(409, 'path_protected'))
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.session.state.notices.files).toEqual({ code: 'path_protected', tone: 'error' })
    expect(calls(t)).toEqual({ ...before, e1: before.e1 + 1 })
    expect(t.refreshes).toEqual([])
  })

  it('C10: worktree_changed re-reads E1 (no resend); a changed fingerprint then reads E8', async () => {
    const t = setup()
    await open(t)
    // Someone changed the workspace behind the screen.
    t.wt.db.status = { ...t.wt.db.status, worktreeToken: 'wt-ext', head: 'h2' }
    const before = calls(t)
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    expect(t.session.state.notices.files?.code).toBe('worktree_changed')
    expect(calls(t).e1).toBe(before.e1 + 1)
    expect(t.session.state.status?.worktreeToken).toBe('wt-ext')
    expect(calls(t).e8).toBe(before.e8 + 1)
    expect(t.refreshes).toEqual([{ groupId: G, closeCompare: false }])
  })

  it('a status_error answer (changed, status unknown) reads E1 again', async () => {
    const t = setup()
    await open(t)
    const real = t.wt.source.stage.getMockImplementation()!
    t.wt.source.stage.mockImplementationOnce(async (...args) => ({ ...(await real(...args)), status: null, statusError: 'status_unavailable' }))
    const before = calls(t)
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(calls(t).e1).toBe(before.e1 + 1)
    expect(t.session.state.status?.files[0].staged).toBe('M')
  })
})

describe('gitWorktreeSession — one action at a time, 403 locks writes', () => {
  it('a second write while one runs is not sent; a 0033 action running blocks writes too', async () => {
    const t = setup()
    await open(t)
    const gate = deferred<void>()
    const real = t.wt.source.stage.getMockImplementation()!
    t.wt.source.stage.mockImplementationOnce(async (...args) => {
      await gate.promise
      return real(...args)
    })
    const first = t.session.stageAll()
    await flushPromises()
    await t.session.stagePath('src/a.ts')
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    expect(t.session.reasons.value.commit).toEqual({ disabled: true, code: null })
    gate.resolve()
    await first
    expect(t.session.state.busy).toBeNull()
  })

  it('a 403 on a write locks every write button with forbidden for the session', async () => {
    const t = setup()
    await open(t)
    t.wt.source.stage.mockRejectedValueOnce(gitError(403, 'forbidden'))
    await t.session.stagePath('src/a.ts')
    expect(t.session.state.forbidden).toBe(true)
    expect(t.session.reasons.value.stage.code).toBe('forbidden')
    expect(t.session.reasons.value.unstageAll.code).toBe('forbidden')
    await t.session.stageAll()
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
  })

  it('reads again when a 0033 action ends, not on every poll', async () => {
    const t = setup()
    await open(t)
    const before = calls(t)
    // Any 0033 action: busy goes from a value back to null.
    t.fake.db.status = status({ baseDirty: { files: ['x.ts'], mergeInProgress: false, aiRun: false } })
    await t.git.refresh()
    await flushPromises()
    expect(calls(t)).toEqual(before)
    t.git.setCommitMessage('base fix')
    await t.git.commit()
    await flushPromises()
    expect(calls(t)).toEqual({ ...before, e1: before.e1 + 1, e8: before.e8 + 1, e10: before.e10 + 1 })
  })
})

// ── Review 0020-TR rev0 (P1): a late success never touches the new context ─────────────────
// Every write answer — success as much as refusal — applies only in the generation it was sent
// in (①). Each write is held at the server, the context moves on, then the answer arrives.

type WriteOp = 'stage' | 'unstage' | 'revert' | 'remove' | 'commit' | 'suggest'
type Move = 'flow' | 'project' | 'gate' | 'dispose'

const P2 = 'p2'
const STAGED_FILE = worktreeFile('s.ts', { staged: 'M', unstaged: null, canStage: false, canUnstage: true })
const NEW_FILE = worktreeFile('n.ts', { inHead: false, unstaged: null, untracked: true, canRevert: false, canRemove: true })
const mixedStatus = () => worktreeStatus({ files: [worktreeFile('src/a.ts'), STAGED_FILE, NEW_FILE] })

const sourceOf = (op: WriteOp) => (op === 'suggest' ? 'suggestCommitMessage' : op) as keyof GitWorktreeSource

// Holds the next answer of `op` until `release()`: the server has done it, the answer is late.
function holdNext(t: ReturnType<typeof setup>, op: WriteOp) {
  const gate = deferred<void>()
  const mock = t.wt.source[sourceOf(op)] as unknown as {
    getMockImplementation(): (...args: unknown[]) => Promise<unknown>
    mockImplementationOnce(fn: (...args: unknown[]) => Promise<unknown>): void
  }
  const real = mock.getMockImplementation()
  mock.mockImplementationOnce(async (...args: unknown[]) => {
    const result = await real(...args)
    await gate.promise
    return result
  })
  return { release: () => gate.resolve() }
}

function startWrite(t: ReturnType<typeof setup>, op: WriteOp): Promise<void> {
  switch (op) {
    case 'stage':
      return t.session.stagePath('src/a.ts')
    case 'unstage':
      return t.session.unstagePath('s.ts')
    case 'revert':
      return t.session.revert('src/a.ts')
    case 'remove':
      return t.session.remove('n.ts')
    case 'commit': {
      t.session.setMessage('feat: A commit')
      const snapshot = t.session.commitSnapshot()
      if (!snapshot) throw new Error('commit not allowed')
      return t.session.commit(snapshot)
    }
    case 'suggest':
      return t.session.suggest()
  }
}

async function moveOn(t: ReturnType<typeof setup>, move: Move) {
  if (move === 'flow') return open(t, { groupId: G2, name: '다른 Flow' })
  if (move === 'project') {
    t.git.setContext(P2, FLOW)
    return flushPromises()
  }
  if (move === 'gate') {
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.git.refresh()
    return flushPromises()
  }
  t.session.dispose()
}

const OPS: WriteOp[] = ['stage', 'unstage', 'revert', 'remove', 'commit', 'suggest']
const MOVES: Move[] = ['flow', 'project', 'gate', 'dispose']
const CASES = OPS.flatMap((op) => MOVES.map((move) => [op, move] as const))

describe('gitWorktreeSession — late write answers after the context moved on (①, C23)', () => {
  it.each(CASES)('%s answered after a %s change changes nothing of the new context', async (op, move) => {
    const t = setup({ wt: { status: mixedStatus() } })
    await open(t)
    expect(t.session.state.providerId).toBe('opus')
    const held = holdNext(t, op)
    const pending = startWrite(t, op)
    await flushPromises()
    expect(t.wt.source[sourceOf(op)]).toHaveBeenCalledTimes(1)
    expect(t.session.state.busy).toBe(op)

    await moveOn(t, move)
    if (move !== 'dispose' && move !== 'gate') {
      // The new context is read and used: a typed message and a selection of its own.
      expect(t.session.state.status).not.toBeNull()
      t.session.setMessage('B typed')
      t.session.setSelected('src/a.ts', true)
    }
    const before = {
      status: JSON.parse(JSON.stringify(t.session.state.status)),
      message: t.session.state.message,
      suggestion: t.session.state.suggestion,
      selected: [...t.session.state.selected],
      notices: { ...t.session.state.notices },
      merge: t.session.state.merge,
      busy: t.session.state.busy,
      calls: calls(t),
      refreshes: t.refreshes.length,
    }

    held.release()
    await pending
    await flushPromises()

    expect(JSON.parse(JSON.stringify(t.session.state.status))).toEqual(before.status)
    expect(t.session.state.message).toBe(before.message)
    expect(t.session.state.suggestion).toEqual(before.suggestion)
    expect(t.session.state.selected).toEqual(before.selected)
    expect(t.session.state.notices).toEqual(before.notices)
    expect(t.session.state.merge).toEqual(before.merge)
    expect(t.session.state.busy).toBe(before.busy)
    // No dependent read and no Center Diff refresh follow the late answer.
    expect(calls(t)).toEqual(before.calls)
    expect(t.refreshes).toHaveLength(before.refreshes)
    if (move === 'gate') expect(t.session.state.status).toBeNull()
  })

  it('a late Stage answer of A never becomes the status of B while B reads its first E1', async () => {
    const t = setup()
    await open(t)
    const held = holdNext(t, 'stage')
    const pending = t.session.stagePath('src/a.ts')
    await flushPromises()
    const firstOfB = deferred<WorktreeStatus>()
    t.wt.source.loadStatus.mockImplementationOnce(() => firstOfB.promise)
    await open(t, { groupId: G2, name: '다른 Flow' })
    expect(t.wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G2)
    held.release()
    await pending
    await flushPromises()
    expect(t.session.state.status).toBeNull()
    expect(t.session.state.statusLoad).toBe('loading')
    expect(t.session.state.notices).toEqual({})
    firstOfB.resolve(worktreeStatus({ groupId: G2, worktreeToken: 'wt-b' }))
    await flushPromises()
    expect(t.session.state.status?.groupId).toBe(G2)
    expect(t.session.state.status?.worktreeToken).toBe('wt-b')
  })

  it('a late AI draft of A never replaces the message typed in B', async () => {
    const t = setup({ wt: { status: mixedStatus() } })
    await open(t)
    const held = holdNext(t, 'suggest')
    const pending = t.session.suggest()
    await flushPromises()
    await open(t, { groupId: G2, name: '다른 Flow' })
    t.session.setMessage('B typed')
    held.release()
    await pending
    await flushPromises()
    expect(t.session.state.message).toBe('B typed')
    expect(t.session.state.suggestion).toBeNull()
  })

  it('a late refusal of A shows no notice in B and reads nothing again', async () => {
    const t = setup({ wt: { status: mixedStatus() } })
    await open(t)
    const answer = deferred<never>()
    t.wt.source.stage.mockImplementationOnce(() => answer.promise)
    const pending = t.session.stagePath('src/a.ts')
    await flushPromises()
    await open(t, { groupId: G2, name: '다른 Flow' })
    const before = calls(t)
    answer.reject(gitError(409, 'worktree_changed'))
    await pending
    await flushPromises()
    expect(t.session.state.notices).toEqual({})
    expect(calls(t)).toEqual(before)
  })
})

describe('gitWorktreeSession — a confirm is bound to the context it was opened in', () => {
  async function opened() {
    const t = setup({ wt: { status: mixedStatus() } })
    await open(t)
    return t
  }

  it('ticket(): project, Flow group and generation while the gate is open; null when closed', async () => {
    const t = await opened()
    const ticket = t.session.ticket()
    expect(ticket).toMatchObject({ projectId: P, groupId: G })
    expect(t.session.isCurrent(ticket!)).toBe(true)
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.git.refresh()
    await flushPromises()
    expect(t.session.ticket()).toBeNull()
    expect(t.session.isCurrent(ticket!)).toBe(false)
    // The gate opening again is a new generation: the old ticket stays stale.
    await t.git.refresh()
    await flushPromises()
    expect(t.session.gateOpen.value).toBe(true)
    expect(t.session.isCurrent(ticket!)).toBe(false)
    expect(t.session.isCurrent(t.session.ticket()!)).toBe(true)
  })

  it.each(MOVES)('a commit snapshot of A sends nothing after a %s change, even with the same index token', async (move) => {
    const t = await opened()
    t.session.setMessage('A commit')
    const snapshot = t.session.commitSnapshot()!
    expect(snapshot.ticket).toMatchObject({ projectId: P, groupId: G })
    await moveOn(t, move)
    if (move === 'gate') {
      await t.git.refresh()
      await flushPromises()
    }
    // B shows the same index token: only the bound context keeps the commit from going there.
    expect(t.session.state.status?.indexToken ?? snapshot.indexToken).toBe(snapshot.indexToken)
    await t.session.commit(snapshot)
    await flushPromises()
    expect(t.wt.source.commit).not.toHaveBeenCalled()
  })

  it.each(MOVES)('revert / remove / AI with a ticket of A send nothing after a %s change', async (move) => {
    const t = await opened()
    const ticket = t.session.ticket()!
    await moveOn(t, move)
    if (move === 'gate') {
      await t.git.refresh()
      await flushPromises()
    }
    await t.session.revert('src/a.ts', ticket)
    await t.session.remove('n.ts', ticket)
    await t.session.suggest(ticket)
    await flushPromises()
    expect(t.wt.source.revert).not.toHaveBeenCalled()
    expect(t.wt.source.remove).not.toHaveBeenCalled()
    expect(t.wt.source.suggestCommitMessage).not.toHaveBeenCalled()
  })

  it('a current ticket sends to its own group', async () => {
    const t = await opened()
    await t.session.revert('src/a.ts', t.session.ticket())
    expect(t.wt.source.revert).toHaveBeenCalledWith(G, ['src/a.ts'], 'wt-1')
    t.session.setMessage('feat: mine')
    await t.session.commit(t.session.commitSnapshot()!)
    expect(t.wt.source.commit).toHaveBeenCalledTimes(1)
    expect(t.wt.source.commit.mock.calls[0][0]).toBe(G)
  })
})
