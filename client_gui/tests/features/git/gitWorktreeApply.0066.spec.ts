import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import type { FlowChangeSet, ProjectGitStatus } from '@/features/project'
import type { GitCenterDiffRefresh, GitSource } from '@/features/git'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession, type GitWorktreeSession } from '@/features/git/gitWorktreeSession'
import type { GitWorktreeSource } from '@/features/git/gitSource'
import type { ManualMergeView } from '@/features/git/types'
import { G, P, createFakeGitSource, gitError, manualSleep, row, status } from './fakeGitSource'
import {
  createFakeWorktreeSource,
  manualMergeView,
  mergeResultRow,
  mergeResults,
  noDetails,
  worktreeFile,
  worktreeStatus,
} from './fakeWorktreeSource'

// flowgate.v02.0066 T#7 — the Base apply part of the Group worktree session (0066 D#2 §3-2-2 ④–⑨,
// §3-3 merge–Fetch, §3-4): target choice and late E8 answers (C15), the merge request (C16),
// approval never a reason (C17), merge results incl. push_rejected (C18), follow-up push (C19),
// Fetch (C20), the `Diff` pane list and target comparison Center Diff (C23 b·c), reads after 0033
// changes (C26) and the merge button after a revert (C28).

const FLOW = { groupId: G, name: '인증 오류 조사' }

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

type T = ReturnType<typeof setup>

const calls = (t: T) => ({
  git: t.fake.source.loadStatus.mock.calls.length,
  e1: t.wt.source.loadStatus.mock.calls.length,
  e8: t.wt.source.loadManualMerge.mock.calls.length,
  e10: t.wt.source.loadMergeResults.mock.calls.length,
  list: t.wt.source.loadTargetChanges.mock.calls.length,
})

async function open(t: T, flow = FLOW) {
  t.git.setContext(P, flow)
  await flushPromises()
}

// E8 answers held per call: `answers[i]` resolves the i-th call made after this.
function holdE8(t: T) {
  const held: Array<{ target: string | null; answer: ReturnType<typeof deferred<ManualMergeView>> }> = []
  t.wt.source.loadManualMerge.mockImplementation(async (_gid: string, target: string | null) => {
    const answer = deferred<ManualMergeView>()
    held.push({ target, answer })
    return answer.promise
  })
  return held
}

function snapshotOf(t: T) {
  const snapshot = t.session.mergeSnapshot()
  if (!snapshot) throw new Error('merge not allowed')
  return snapshot
}

const changes = (target: string, paths: string[]): FlowChangeSet => ({
  groupId: G,
  branch: 'flowgate_v02_0008',
  commit: 'h1',
  baseBranch: target,
  changes: paths.map((path) => ({ path, status: 'M' })),
})

describe('gitWorktreeApply — merge target (C15)', () => {
  it('the first E8 picks default_target; the choices are E8 targets only; an ineligible one cannot be chosen', async () => {
    const t = setup({
      wt: {
        merge: manualMergeView({
          targets: [
            { name: 'v0.2', kind: 'base', eligible: true, blockedReason: null, isWorkBase: true },
            { name: 'release', kind: 'local', eligible: true, blockedReason: null, isWorkBase: false },
            { name: 'remote-x', kind: 'local', eligible: false, blockedReason: 'remote_only', isWorkBase: false },
          ],
        }),
      },
    })
    await open(t)
    expect(t.session.state.mergeTarget).toBe('v0.2')
    expect(t.session.state.mergeTargets.map((x) => x.name)).toEqual(['v0.2', 'release', 'remote-x'])
    expect(t.session.reasons.value.merge).toEqual({ disabled: false, code: null })
    expect(t.session.applySummary.value).toEqual({ kind: 'ahead', n: 1 })
    const before = calls(t)
    t.session.setMergeTarget('remote-x')
    t.session.setMergeTarget('unknown')
    await flushPromises()
    expect(t.session.state.mergeTarget).toBe('v0.2')
    expect(calls(t)).toEqual(before)
  })

  it('A→B: the A judgment leaves at once ("확인 중"); a late A answer is dropped; B applies', async () => {
    const t = setup()
    await open(t)
    const held = holdE8(t)
    t.session.refresh()
    await flushPromises()
    expect(held.map((h) => h.target)).toEqual(['v0.2'])
    // B is chosen while A's re-read is still out.
    t.session.setMergeTarget('release')
    await flushPromises()
    expect(held.map((h) => h.target)).toEqual(['v0.2', 'release'])
    expect(t.session.state.merge).toBeNull()
    expect(t.session.state.mergeLoad).toBe('loading')
    expect(t.session.reasons.value.merge).toEqual({ disabled: true, code: null })
    expect(t.session.applySummary.value).toEqual({ kind: 'checking' })
    expect(t.session.mergeSnapshot()).toBeNull()
    // A answers late with a mergeable judgment: dropped.
    held[0].answer.resolve(manualMergeView({ aheadOfTarget: 9 }))
    await flushPromises()
    expect(t.session.state.merge).toBeNull()
    expect(t.session.state.mergeTarget).toBe('release')
    expect(t.session.reasons.value.merge.disabled).toBe(true)
    // B answers: its own numbers and reason.
    held[1].answer.resolve(
      manualMergeView({
        canMerge: false,
        aheadOfTarget: 3,
        blocked: [{ code: 'merge_conflict_open', reason: null, details: noDetails() }],
      }),
    )
    await flushPromises()
    expect(t.session.state.merge?.aheadOfTarget).toBe(3)
    expect(t.session.reasons.value.merge).toEqual({ disabled: true, code: 'merge_conflict_open' })
    expect(t.session.applySummary.value).toEqual({ kind: 'ahead', n: 3 })
  })

  it('a chosen target that is no longer eligible empties the selection: no_target, no judgment', async () => {
    const t = setup()
    await open(t)
    t.wt.db.merge = manualMergeView({
      targets: [
        { name: 'v0.2', kind: 'base', eligible: true, blockedReason: null, isWorkBase: true },
        { name: 'release', kind: 'local', eligible: false, blockedReason: 'workspace_busy', isWorkBase: false },
      ],
    })
    t.session.setMergeTarget('release')
    await flushPromises()
    expect(t.session.state.mergeTarget).toBeNull()
    expect(t.session.state.merge).toBeNull()
    expect(t.session.reasons.value.merge).toEqual({ disabled: true, code: 'no_target' })
    // A refresh keeps it empty (no default picked over the person's choice) and still settles.
    t.session.refresh()
    await flushPromises()
    expect(t.session.state.mergeTarget).toBeNull()
    expect(t.session.state.mergeLoad).toBe('ready')
  })

  it('no eligible default target: nothing is chosen and the merge says no_target', async () => {
    const t = setup({
      wt: {
        merge: manualMergeView({
          targets: [{ name: 'v0.2', kind: 'base', eligible: false, blockedReason: 'owner_mismatch', isWorkBase: true }],
        }),
      },
    })
    await open(t)
    expect(t.session.state.mergeTarget).toBeNull()
    expect(t.session.reasons.value.merge.code).toBe('no_target')
  })

  it('⑥ a merge dialog of A sends nothing once another judgment or target is on screen', async () => {
    const t = setup()
    await open(t)
    const snapshot = snapshotOf(t)
    expect(snapshot).toMatchObject({ target: 'v0.2', ahead: 1, recovery: false, push: false })
    // A re-read lands a newer judgment for the same target.
    t.session.refresh()
    await flushPromises()
    await t.session.merge(snapshot)
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
    expect(t.session.state.notices.apply?.code).toBe('judgment_changed')
    // Target changed after the dialog opened.
    const again = snapshotOf(t)
    t.session.setMergeTarget('release')
    await flushPromises()
    await t.session.merge(again)
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
  })
})

describe('gitWorktreeApply — the merge request (C16) and approval (C17)', () => {
  it('unchecked → E9 push=false; checked → push=true; the target is the fixed one', async () => {
    const t = setup()
    await open(t)
    await t.session.merge(snapshotOf(t))
    expect(t.wt.source.manualMerge).toHaveBeenLastCalledWith(G, 'v0.2', false)
    t.session.setPushAfterMerge(true)
    t.session.setMergeTarget('release')
    await flushPromises()
    const snapshot = snapshotOf(t)
    expect(snapshot).toMatchObject({ target: 'release', push: true, ahead: 1 })
    await t.session.merge(snapshot)
    expect(t.wt.source.manualMerge).toHaveBeenLastCalledWith(G, 'release', true)
    // Never the Base checkout push or finalize.
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(t.fake.source.finalize).not.toHaveBeenCalled()
  })

  it('a pending review, a final-approval binding or an open gate never disable a merge (C17)', async () => {
    const t = setup({
      wt: {
        merge: manualMergeView({
          approval: { acDocId: 'p.m.0001.0009-AC', status: 'pending_review', finalApprovalBound: true },
          graph: { runPresent: true, blocker: null, gateOpen: true },
          admissionBasis: 'no_wf_done',
        }),
        status: worktreeStatus({ gitState: { status: 'waiting', rootWfDone: false, finalApprovalBound: true } }),
      },
      gitStatus: status({ pending: [row({ status: 'waiting', mergeId: null })] }),
    })
    await open(t)
    expect(t.session.reasons.value.merge).toEqual({ disabled: false, code: null })
    await t.session.merge(snapshotOf(t))
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(1)
  })
})

describe('gitWorktreeApply — merge results (C18)', () => {
  it('merged: notice, git/status and E1 / E8 / E10 read again, the area stays', async () => {
    const t = setup()
    await open(t)
    const before = calls(t)
    t.session.setPushAfterMerge(true)
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply).toEqual({ code: 'merged_pushed', params: { target: 'v0.2' } })
    expect(calls(t)).toEqual({ ...before, git: before.git + 1, e1: before.e1 + 1, e8: before.e8 + 1, e10: before.e10 + 1 })
    expect(t.session.state.status).not.toBeNull()
    expect(t.session.state.busy).toBeNull()
  })

  it('a recovered merge says so; a conflict hands over to the 0033 card through git/status', async () => {
    const t = setup()
    await open(t)
    const merged = {
      status: 'merged',
      groupStatus: 'none',
      worktreeKept: true,
      action: 'merge_only',
      targetBranch: 'v0.2',
      mergeCommit: 'aaaaaaa',
      mergeCommitSha: 'a'.repeat(40),
      pushed: false,
      recovered: true,
      mergeId: 11,
      conflictFileCount: 0,
      excludedArtifactCount: 0,
      jobId: 'job-1',
    }
    t.wt.source.manualMerge.mockResolvedValueOnce(merged)
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply).toEqual({ code: 'recovered', params: { target: 'v0.2' } })

    const before = calls(t)
    t.fake.db.status = status({ pending: [row({ status: 'conflict', mergeId: 7 })] })
    t.wt.source.manualMerge.mockResolvedValueOnce({
      ...merged,
      status: 'conflict',
      groupStatus: 'conflict',
      mergeCommit: null,
      mergeCommitSha: null,
      recovered: false,
      mergeId: 7,
      conflictFileCount: 2,
    })
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply?.code).toBe('merge_conflict')
    expect(calls(t).git).toBe(before.git + 1)
    expect(t.git.groupCard.value).toBe('conflict')
  })

  it('nothing_to_merge: one notice per reason; already_recorded points at its E10 row', async () => {
    const t = setup({ wt: { results: mergeResults({ results: [mergeResultRow({ mergeId: 11 })] }) } })
    await open(t)
    const refusal = (reason: string | null, mergeId: number | null = null) =>
      Object.assign(gitError(409, 'nothing_to_merge'), {
        response: { status: 409, data: { ok: false, error: { code: 'nothing_to_merge', message: 'x', details: { reason, merge_id: mergeId } } } },
      })
    for (const [reason, code] of [
      [null, 'nothing_to_merge'],
      ['target_synced_contains', 'target_synced_contains'],
    ] as const) {
      t.wt.source.manualMerge.mockRejectedValueOnce(refusal(reason))
      await t.session.merge(snapshotOf(t))
      await flushPromises()
      expect(t.session.state.notices.apply?.code).toBe(code)
    }
    const before = calls(t)
    t.wt.source.manualMerge.mockRejectedValueOnce(refusal('already_recorded', 11))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply?.code).toBe('already_recorded')
    expect(t.session.state.highlightMergeId).toBe(11)
    expect(t.session.pushRow.value?.mergeId).toBe(11)
    expect(calls(t)).toMatchObject({ e8: before.e8 + 1, e10: before.e10 + 1 })
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(3)
  })

  it('uncommitted_changes (+ protected), a queued job and a target refusal: notices, reads, no resend', async () => {
    const t = setup()
    await open(t)
    const withDetails = (status: number, code: string, details: Record<string, unknown>) =>
      Object.assign(gitError(status, code), {
        response: { status, data: { ok: false, error: { code, message: 'x', details } } },
      })
    let before = calls(t)
    t.wt.source.manualMerge.mockRejectedValueOnce(withDetails(409, 'uncommitted_changes', { count: 4, protected_uncommitted: 2 }))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply).toEqual({ code: 'uncommitted_protected', tone: 'error', params: { n: 4, protected: 2 } })
    expect(calls(t)).toMatchObject({ e1: before.e1 + 1, e8: before.e8 + 1 })

    before = calls(t)
    t.wt.source.manualMerge.mockRejectedValueOnce(withDetails(409, 'git_busy', { job_id: 'job-7' }))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply?.code).toBe('queued')

    t.wt.source.manualMerge.mockRejectedValueOnce(withDetails(409, 'merge_target_busy', {}))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply).toEqual({ code: 'merge_target_unavailable', tone: 'error', params: { code: 'merge_target_busy' } })
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(409, 'graph_execution_active'))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply?.code).toBe('graph_execution_active')
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(4)
  })

  it('push_rejected of a new merge: no "rewound" claim; after the re-reads the line says it is not in the target', async () => {
    const t = setup()
    await open(t)
    t.session.setPushAfterMerge(true)
    const before = calls(t)
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    const held = holdE8(t)
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.notices.apply).toEqual({ code: 'merge_push_rejected', tone: 'error' })
    // Until the re-read judgment is in, only the first line.
    expect(t.session.state.rejectHint).toBe('pending')
    expect(calls(t)).toMatchObject({ git: before.git + 1, e1: before.e1 + 1, e8: before.e8 + 1, e10: before.e10 + 1 })
    expect(held.map((h) => h.target)).toEqual(['v0.2'])
    held[0].answer.resolve(manualMergeView({ aheadOfTarget: 1, recovery: false }))
    await flushPromises()
    expect(t.session.state.rejectHint).toBe('rewound')
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(1)
  })

  it('push_rejected while recovering an unpushed merge: the re-read says recovery; pressing again sends the same E9 once', async () => {
    const t = setup({
      wt: {
        merge: manualMergeView({ aheadOfTarget: 0, recovery: true, mergeNeeded: true }),
        results: mergeResults({ groupStatus: 'none' }),
      },
    })
    await open(t)
    t.session.setPushAfterMerge(true)
    const first = snapshotOf(t)
    expect(first).toMatchObject({ ahead: 0, recovery: true, push: true })
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 0, recovery: true, mergeNeeded: true })
    t.wt.db.results = mergeResults({ groupStatus: 'waiting' })
    t.wt.db.status = worktreeStatus({ gitState: { status: 'waiting', rootWfDone: false, finalApprovalBound: false } })
    await t.session.merge(first)
    await flushPromises()
    expect(t.session.state.rejectHint).toBe('recovery')
    expect(t.session.state.results?.groupStatus).toBe('waiting')
    // Nothing is sent again by itself; the merge is offered again as a recovery.
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(1)
    expect(t.session.reasons.value.merge).toEqual({ disabled: false, code: null })
    const again = snapshotOf(t)
    expect(again).toMatchObject({ target: 'v0.2', recovery: true, push: true })
    await t.session.merge(again)
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(2)
    expect(t.wt.source.manualMerge).toHaveBeenLastCalledWith(G, 'v0.2', true)
    // The 0033 push failure record is never made by a manual merge.
    expect(t.git.state.windows).toEqual([])
  })

  it('a failed re-read after push_rejected leaves no hint line', async () => {
    const t = setup()
    await open(t)
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    t.wt.source.loadManualMerge.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.session.merge(snapshotOf(t))
    await flushPromises()
    expect(t.session.state.rejectHint).toBeNull()
    expect(t.session.state.mergeLoad).toBe('error')
    expect(t.session.reasons.value.merge.code).toBe('merge_unread')
  })
})

describe('gitWorktreeApply — follow-up push (C19)', () => {
  const two = () =>
    mergeResults({
      results: [
        mergeResultRow({ mergeId: 12, target: { targetBranch: 'release', isProjectBase: false }, mergeCommit: 'b'.repeat(40) }),
        mergeResultRow({ mergeId: 11 }),
      ],
    })

  it('one candidate: its record only (merge_id, merge_commit), never a branch name or /git/push', async () => {
    const t = setup({ wt: { results: mergeResults({ results: [mergeResultRow()] }) } })
    await open(t)
    expect(t.session.pushCandidates.value).toHaveLength(1)
    const snapshot = t.session.pushSnapshot()
    expect(snapshot).toMatchObject({ mergeId: 11, mergeCommit: 'a'.repeat(40), target: 'v0.2', isProjectBase: true })
    const before = calls(t)
    await t.session.pushResult(snapshot!)
    await flushPromises()
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledWith(G, 11, 'a'.repeat(40))
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(t.session.state.notices.push).toEqual({ code: 'pushed', params: { target: 'v0.2' } })
    expect(calls(t)).toMatchObject({ git: before.git + 1, e10: before.e10 + 1, e1: before.e1, e8: before.e8 })
  })

  it('two or more: the newest by default, the picked one when chosen', async () => {
    const t = setup({ wt: { results: two() } })
    await open(t)
    expect(t.session.pushRow.value?.mergeId).toBe(12)
    t.session.setPushChoice(11)
    expect(t.session.pushRow.value?.mergeId).toBe(11)
    await t.session.pushResult(t.session.pushSnapshot()!)
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledWith(G, 11, 'a'.repeat(40))
  })

  it('no candidate: unmeasured / blocked rows give the disabled reason; published rows give none', async () => {
    const unmeasured = setup({
      wt: { results: mergeResults({ results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'unmeasured' })] }) },
    })
    await open(unmeasured)
    expect(unmeasured.session.pushRow.value).toBeNull()
    expect(unmeasured.session.reasons.value.push).toEqual({ disabled: true, code: 'unmeasured' })
    expect(unmeasured.session.pushSnapshot()).toBeNull()

    const blocked = setup({
      wt: {
        results: mergeResults({
          groupBlocker: 'conflict',
          results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'group_blocked' })],
        }),
      },
    })
    await open(blocked)
    expect(blocked.session.reasons.value.push.code).toBe('group_blocked')
  })

  it('stale_target / merge_target_moved / merge_result_missing read E10 again, never another row', async () => {
    const t = setup({ wt: { results: two() } })
    await open(t)
    for (const code of ['stale_target', 'merge_target_moved', 'merge_result_missing']) {
      const before = calls(t)
      t.wt.source.pushMergeResult.mockRejectedValueOnce(gitError(409, code))
      await t.session.pushResult(t.session.pushSnapshot()!)
      await flushPromises()
      expect(t.session.state.notices.push).toEqual({ code: 'record_changed', tone: 'error' })
      expect(calls(t).e10).toBe(before.e10 + 1)
    }
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledTimes(3)
    expect(t.wt.source.pushMergeResult.mock.calls.every((c) => c[1] === 12)).toBe(true)
  })

  it('push_rejected keeps the merge result; already_published says so; a newer E10 voids the dialog', async () => {
    const t = setup({ wt: { results: mergeResults({ results: [mergeResultRow()] }) } })
    await open(t)
    t.wt.source.pushMergeResult.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    await t.session.pushResult(t.session.pushSnapshot()!)
    await flushPromises()
    expect(t.session.state.notices.push).toEqual({ code: 'push_rejected_kept', tone: 'error' })

    t.wt.source.pushMergeResult.mockResolvedValueOnce({
      pushedRef: null,
      pushedHead: null,
      targetBranch: 'v0.2',
      isProjectBase: true,
      mergeId: 11,
      mergeCommit: 'a'.repeat(40),
      alreadyPublished: true,
      jobId: null,
      phase: 'done',
    })
    await t.session.pushResult(t.session.pushSnapshot()!)
    await flushPromises()
    expect(t.session.state.notices.push).toEqual({ code: 'already_published', params: { target: 'v0.2' } })

    const snapshot = t.session.pushSnapshot()!
    t.session.refresh()
    await flushPromises()
    await t.session.pushResult(snapshot)
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledTimes(2)
    expect(t.session.state.notices.push?.code).toBe('push_changed')
  })
})

describe('gitWorktreeApply — Fetch (C20)', () => {
  it('E12 only; then git/status, E8 and E10 again (no E1); the working files are untouched', async () => {
    const t = setup()
    await open(t)
    const before = calls(t)
    await t.session.fetchRemote()
    await flushPromises()
    expect(t.wt.source.fetchRefs).toHaveBeenCalledWith(P, G)
    expect(t.session.state.notices.apply).toEqual({ code: 'fetched' })
    expect(calls(t)).toEqual({ ...before, git: before.git + 1, e8: before.e8 + 1, e10: before.e10 + 1 })
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(t.wt.source.stage).not.toHaveBeenCalled()
  })

  it('a refused Fetch is a notice with its code; works without a workspace; read-only disables it', async () => {
    const t = setup({ wt: { status: worktreeStatus({ worktreeAvailable: false, files: [], worktreeToken: null, indexToken: null }) } })
    await open(t)
    expect(t.session.reasons.value.fetch).toEqual({ disabled: false, code: null })
    t.wt.source.fetchRefs.mockRejectedValueOnce(gitError(409, 'git_busy'))
    await t.session.fetchRemote()
    expect(t.session.state.notices.apply).toEqual({ code: 'git_busy', tone: 'error' })
    t.wt.source.fetchRefs.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.session.fetchRemote()
    expect(t.session.state.notices.apply).toEqual({ code: 'git_error', tone: 'error' })
    t.git.setReadOnly(true)
    expect(t.session.reasons.value.fetch).toEqual({ disabled: true, code: 'read_only' })
    await t.session.fetchRemote()
    expect(t.wt.source.fetchRefs).toHaveBeenCalledTimes(2)
  })
})

describe('gitWorktreeApply — `Diff` pane list and target comparison Center Diff (C23 b·c)', () => {
  it('opening `Diff` reads /changes?base=<chosen target> once; folding the area closes the pane', async () => {
    const t = setup({ wt: { targetChanges: { 'v0.2': changes('v0.2', ['a.ts', 'b.ts']) } } })
    await open(t)
    expect(calls(t).list).toBe(0)
    t.session.toggleApply()
    t.session.toggleInfo('diff')
    await flushPromises()
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledWith(P, G, 'v0.2')
    expect(t.session.state.targetList).toEqual({ target: 'v0.2', changes: changes('v0.2', ['a.ts', 'b.ts']).changes })
    t.session.toggleInfo('status')
    expect(t.session.state.infoPane).toBe('status')
    t.session.toggleApply()
    expect(t.session.state.infoPane).toBeNull()
  })

  it('A→B with the pane open: the A list goes at once, B is read once, a late A list is dropped', async () => {
    const t = setup()
    await open(t)
    const lists: Array<{ target: string; answer: ReturnType<typeof deferred<FlowChangeSet>> }> = []
    t.wt.source.loadTargetChanges.mockImplementation(async (_p: string, _g: string, target: string) => {
      const answer = deferred<FlowChangeSet>()
      lists.push({ target, answer })
      return answer.promise
    })
    t.session.toggleApply()
    t.session.toggleInfo('diff')
    await flushPromises()
    lists[0].answer.resolve(changes('v0.2', ['a.ts']))
    await flushPromises()
    expect(t.session.state.targetList?.target).toBe('v0.2')
    // Read again for A (refresh), then B is chosen before that answer.
    t.session.refresh()
    await flushPromises()
    t.session.setMergeTarget('release')
    expect(t.session.state.targetList).toBeNull()
    expect(t.session.state.targetListLoad).toBe('loading')
    await flushPromises()
    expect(lists.map((l) => l.target)).toEqual(['v0.2', 'v0.2', 'release'])
    lists[1].answer.resolve(changes('v0.2', ['late.ts']))
    await flushPromises()
    expect(t.session.state.targetList).toBeNull()
    lists[2].answer.resolve(changes('release', ['r.ts']))
    await flushPromises()
    expect(t.session.state.targetList).toEqual({ target: 'release', changes: [{ path: 'r.ts', status: 'M' }] })
  })

  it('(c) a target change closes a comparison Center Diff of this group; a status change only re-reads', async () => {
    const t = setup()
    await open(t)
    t.session.setMergeTarget('release')
    expect(t.refreshes).toEqual([{ groupId: G, closeCompare: true }])
    await flushPromises()
    await t.session.stagePath('src/a.ts')
    await flushPromises()
    expect(t.refreshes.at(-1)).toEqual({ groupId: G, closeCompare: false })
  })

  it('C28(c): a new status fingerprint re-reads the open list with the same target (kept while reading)', async () => {
    const t = setup({ wt: { targetChanges: { 'v0.2': changes('v0.2', ['src/a.ts']) } } })
    await open(t)
    t.session.toggleApply()
    t.session.toggleInfo('diff')
    await flushPromises()
    const before = calls(t)
    const reread = deferred<FlowChangeSet>()
    t.wt.source.loadTargetChanges.mockImplementationOnce(() => reread.promise)
    await t.session.revert('src/a.ts')
    await flushPromises()
    expect(calls(t).list).toBe(before.list + 1)
    expect(t.wt.source.loadTargetChanges).toHaveBeenLastCalledWith(P, G, 'v0.2')
    expect(t.session.state.targetList?.target).toBe('v0.2')
    expect(t.session.state.targetListLoad).toBe('loading')
    reread.resolve(changes('v0.2', []))
    await flushPromises()
    expect(t.session.state.targetList?.changes).toEqual([])
  })

  it('a Flow switch drops a late list answer and resets the area (folded, unchecked, no pane)', async () => {
    const t = setup()
    await open(t)
    const late = deferred<FlowChangeSet>()
    t.wt.source.loadTargetChanges.mockImplementationOnce(() => late.promise)
    t.session.toggleApply()
    t.session.setPushAfterMerge(true)
    t.session.toggleInfo('diff')
    await flushPromises()
    await open(t, { groupId: 'p.m.0002', name: 'B' })
    late.resolve(changes('v0.2', ['a.ts']))
    await flushPromises()
    expect(t.session.state.targetList).toBeNull()
    expect(t.session.state).toMatchObject({ applyOpen: false, pushAfterMerge: false, infoPane: null })
  })
})

describe('gitWorktreeApply — reads after 0033 changes (C26) and the merge button after a revert (C28)', () => {
  it('a changed pending row reads E1 / E8 / E10 once; a poll with the same state reads nothing', async () => {
    const t = setup()
    await open(t)
    const before = calls(t)
    t.fake.db.status = status({ pending: [row({ status: 'waiting', mergeId: null })] })
    await t.git.refresh()
    await flushPromises()
    expect(calls(t)).toEqual({ ...before, git: before.git + 1, e1: before.e1 + 1, e8: before.e8 + 1, e10: before.e10 + 1 })
    const again = calls(t)
    await t.git.refresh()
    await flushPromises()
    expect(calls(t)).toEqual({ ...again, git: again.git + 1 })
  })

  it('C28(a): reverting the last uncommitted file turns the merge button on without a refresh', async () => {
    const t = setup({
      wt: {
        merge: manualMergeView({
          canMerge: false,
          aheadOfTarget: 2,
          blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 1 }) }],
          uncommitted: { blocking: 1, counts: { unstaged: 1 }, protectedUncommitted: 0, reason: null },
        }),
      },
    })
    await open(t)
    expect(t.session.reasons.value.merge).toEqual({ disabled: true, code: 'uncommitted_changes', params: { n: 1, protected: 0 } })
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 2 })
    await t.session.revert('src/a.ts')
    await flushPromises()
    expect(t.session.reasons.value.merge).toEqual({ disabled: false, code: null })
    expect(t.session.applySummary.value).toEqual({ kind: 'ahead', n: 2 })
  })
})

describe('gitWorktreeApply — header word (§3-4(1))', () => {
  it('merging > conflict > remote pending > checking > ahead > recovery > nothing', async () => {
    const t = setup({
      wt: {
        status: worktreeStatus({ gitState: { status: 'merging', rootWfDone: false, finalApprovalBound: false } }),
        results: mergeResults({ results: [mergeResultRow()] }),
      },
    })
    await open(t)
    expect(t.session.applySummary.value).toEqual({ kind: 'merging' })
    t.wt.db.status = worktreeStatus({ gitState: { status: 'conflict', rootWfDone: false, finalApprovalBound: false } })
    t.session.refresh()
    await flushPromises()
    expect(t.session.applySummary.value).toEqual({ kind: 'conflict' })
    t.wt.db.status = worktreeStatus()
    t.session.refresh()
    await flushPromises()
    expect(t.session.applySummary.value).toEqual({ kind: 'remote_pending', n: 1 })
    t.wt.db.results = mergeResults()
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 0, recovery: true })
    t.session.refresh()
    await flushPromises()
    expect(t.session.applySummary.value).toEqual({ kind: 'recovery' })
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 0, canMerge: false, blocked: [{ code: 'nothing_to_merge', reason: null, details: noDetails() }] })
    t.session.refresh()
    await flushPromises()
    expect(t.session.applySummary.value).toEqual({ kind: 'nothing' })
    expect(t.session.reasons.value.merge.code).toBe('nothing_to_merge')
    // The display-only committed range never makes the number.
    t.wt.db.status = worktreeStatus({ committed: { commitCount: 5, changedFileCount: 3 } })
    t.session.refresh()
    await flushPromises()
    expect(t.session.applySummary.value).toEqual({ kind: 'nothing' })
  })
})
