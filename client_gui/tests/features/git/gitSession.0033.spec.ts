import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import {
  G,
  P,
  conflicts,
  createFakeGitSource,
  gitError,
  manualSleep,
  networkError,
  resolvingRun,
  review,
  row,
  status,
  trConflicts,
} from './fakeGitSource'

// flowgate.v02.0033 — Git session rules (0033-D §3, §4) on an in-memory source.

let session: GitSession | null = null

afterEach(() => {
  session?.dispose()
  session = null
})

async function open(fake: ReturnType<typeof createFakeGitSource>, opts: { sleep?: (ms: number) => Promise<void> } = {}) {
  session = createGitSession({ source: fake.source, sleep: opts.sleep ?? manualSleep().sleep, now: () => new Date('2026-10-07T01:02:03Z') })
  session.setContext(P, { groupId: G, name: '인증 오류 조사' })
  await flushPromises()
  return session
}

describe('header and nothing to do', () => {
  it('shows no card and no badge when there is nothing to do', async () => {
    const s = await open(createFakeGitSource())
    expect(s.groupCard.value).toBeNull()
    expect(s.dirtyView.value).toBeNull()
    expect(s.badge.value).toBeNull()
    expect(s.workBranch.value).toBe('flowgate_v02_0008')
    expect(s.state.status?.baseBranch).toBe('v0.2')
  })

  it('prefers the Flow branch and falls back to the group slot', async () => {
    const fake = createFakeGitSource()
    session = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
    session.setContext(P, { groupId: G, name: 'f', branch: 'flow-branch' })
    await flushPromises()
    expect(session.workBranch.value).toBe('flow-branch')
  })

  it('reads an unreadable status as unreadable and hides every card', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.loadStatus.mockRejectedValueOnce(gitError(404, 'not_found'))
    const s = await open(fake)
    expect(s.state.statusError).toBe('unreadable')
    expect(s.groupCard.value).toBeNull()
  })
})

describe('group cards', () => {
  it('a conflict without review state is the conflict card with its files', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('conflict')
    expect(fake.source.fetchConflicts).toHaveBeenCalledWith(G, 7)
    expect(s.state.conflict?.files).toHaveLength(5)
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
  })

  it('only the active Flow group counts', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ groupId: 'p.m.0002' })] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBeNull()
  })

  it('a running resolve_conflict run wins over the review state', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }), ai: resolvingRun })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('ai')
    expect(s.badge.value).toEqual({ tone: 'progress', text: '…' })
    expect(fake.source.fetchReview).not.toHaveBeenCalled()
  })

  it.each(['resolved_pending_review', 're_review', 'applying', 'reconciling'])('%s is the review card', async (state) => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: state })] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('review')
    expect(fake.source.fetchReview).toHaveBeenCalledWith(G, 7)
    expect(fake.source.fetchProviders).toHaveBeenCalledWith(P)
    expect(s.badge.value?.tone).toBe('danger')
  })

  it.each(['open', 'resolved'])('a TR revert conflict (%s) is not a review', async (state) => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: state })] }) })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('unsupported')
    expect(fake.source.fetchReview).not.toHaveBeenCalled()
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
  })

  it('a TR revert conflict as git/status sends it today (review_state null) is told by the conflicts kind', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: null })] }), conflicts: trConflicts() })
    const s = await open(fake)
    expect(s.groupCard.value).toBe('unsupported')
    expect(s.conflictActionable.value).toBe(false)
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
    expect(fake.source.fetchReview).not.toHaveBeenCalled()
    await s.startAi()
    await s.abortMerge()
    s.openConflictWindow('충돌 해결')
    expect(fake.source.startConflictAi).not.toHaveBeenCalled()
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
    expect(s.state.windows).toHaveLength(0)
  })

  it('no conflict action before the conflicts answer says which kind of session it is', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    let release: () => void = () => undefined
    fake.source.fetchConflicts.mockImplementationOnce(
      () => new Promise((resolve) => (release = () => resolve(conflicts()))),
    )
    const s = await open(fake)
    expect(s.groupCard.value).toBe('conflict')
    expect(s.conflictActionable.value).toBe(false)
    await s.startAi()
    await s.abortMerge()
    expect(fake.source.startConflictAi).not.toHaveBeenCalled()
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
    release()
    await flushPromises()
    expect(s.conflictActionable.value).toBe(true)
  })

  it('a conflicts read that fails leaves the conflict actions off', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.fetchConflicts.mockRejectedValueOnce(gitError(500, 'internal'))
    const s = await open(fake)
    expect(s.groupCard.value).toBe('conflict')
    expect(s.conflictActionable.value).toBe(false)
  })
})

describe('conflict actions', () => {
  it('AI 해결 starts a resolve_conflict run and moves to the AI card', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    fake.db.ai = resolvingRun
    await s.startAi()
    expect(fake.source.startConflictAi).toHaveBeenCalledWith(G, 7)
    expect(s.groupCard.value).toBe('ai')
  })

  it('a 409 on start says a run is already going and re-reads', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.startConflictAi.mockRejectedValueOnce(gitError(409, 'run_active'))
    const s = await open(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    await s.startAi()
    expect(s.state.notices.conflict).toEqual({ code: 'ai_running' })
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })

  it('abort calls the merge abort and the status is read again', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    fake.db.status = status({ pending: [row({ status: 'waiting', mergeId: null })] })
    await s.abortMerge()
    expect(fake.source.abortMerge).toHaveBeenCalledWith(G, 7)
    expect(s.groupCard.value).toBe('hold')
  })

  it('abort errors are shown by code; git_busy has its own text', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.abortMerge.mockRejectedValueOnce(gitError(409, 'git_busy'))
    const s = await open(fake)
    await s.abortMerge()
    expect(s.state.notices.conflict).toEqual({ code: 'git_busy' })
  })

  it('an unknown code is the generic failure with the code, never the server message', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.abortMerge.mockRejectedValueOnce(gitError(500, 'odd_code'))
    const s = await open(fake)
    await s.abortMerge()
    expect(s.state.notices.conflict).toEqual({ code: 'failed', params: { code: 'odd_code' } })
  })

  it('a 403 disables every action afterwards', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.abortMerge.mockRejectedValueOnce(gitError(403, 'forbidden'))
    const s = await open(fake)
    await s.abortMerge()
    expect(s.state.notices.conflict).toEqual({ code: 'forbidden' })
    expect(s.canAct.value).toBe(false)
  })

  it('stop cancels the run from GET active; the AI end re-reads the conflicts', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), ai: resolvingRun })
    const s = await open(fake)
    const conflictReads = fake.source.fetchConflicts.mock.calls.length
    fake.db.ai = { active: false, runId: null, status: null, actionScope: null }
    await s.cancelAi()
    expect(fake.source.cancelAiRun).toHaveBeenCalledWith('run-1')
    expect(s.groupCard.value).toBe('conflict')
    expect(fake.source.fetchConflicts.mock.calls.length).toBeGreaterThan(conflictReads)
  })

  it('polls while the AI card is up and stops when the run ends', async () => {
    const clock = manualSleep()
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), ai: resolvingRun })
    const s = await open(fake, { sleep: clock.sleep })
    expect(clock.pending).toBe(1)
    const reads = fake.source.loadStatus.mock.calls.length
    clock.tick()
    await flushPromises()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
    fake.db.ai = { active: false, runId: null, status: null, actionScope: null }
    clock.tick()
    await flushPromises()
    expect(s.groupCard.value).toBe('conflict')
    expect(clock.pending).toBe(0)
  })

  it('keeps polling past 20 minutes for as long as the run lasts', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), ai: resolvingRun })
    let naps = 0
    // 600 × 2.5 s = 25 minutes; the run ends only then.
    const sleep = vi.fn(async () => {
      naps += 1
      if (naps === 600) fake.db.ai = { active: false, runId: null, status: null, actionScope: null }
    })
    const s = await open(fake, { sleep })
    await flushPromises()
    expect(naps).toBe(600)
    expect(fake.source.loadStatus.mock.calls.length).toBeGreaterThan(600)
    expect(s.groupCard.value).toBe('conflict')
  })
})

describe('merge review (Q 394)', () => {
  async function reviewSession(init: Parameters<typeof createFakeGitSource>[0] = {}) {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }), ...init })
    const s = await open(fake)
    return { fake, s }
  }

  it('approve sends a UUID attempt id and the review fingerprint', async () => {
    const { fake, s } = await reviewSession()
    fake.db.status = status()
    await s.approve()
    const [gid, mid, attemptId, fingerprint] = fake.source.approveReview.mock.calls[0]
    expect([gid, mid, fingerprint]).toEqual([G, 7, 'fp-1'])
    expect(attemptId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(s.groupCard.value).toBeNull()
  })

  it('a network failure resends the same attempt id; any other answer gets a new one', async () => {
    const { fake, s } = await reviewSession()
    fake.source.approveReview.mockRejectedValueOnce(networkError())
    await s.approve()
    expect(s.state.notices.review).toEqual({ code: 'network' })
    fake.source.approveReview.mockResolvedValueOnce({ status: 're_review', reviewState: 're_review', reconciliationKind: 'push_rejected', errorCount: 0 })
    await s.approve()
    const first = fake.source.approveReview.mock.calls[0][2]
    expect(fake.source.approveReview.mock.calls[1][2]).toBe(first)
    await s.approve()
    expect(fake.source.approveReview.mock.calls[2][2]).not.toBe(first)
  })

  it('re_review after a rejected push is not a remote apply failure record', async () => {
    const { fake, s } = await reviewSession()
    fake.source.approveReview.mockResolvedValueOnce({ status: 're_review', reviewState: 're_review', reconciliationKind: 'push_rejected', errorCount: 0 })
    fake.db.status = status({ pending: [row({ reviewState: 're_review' })] })
    fake.db.review = review({ reviewState: 're_review', fingerprint: 'fp-2', reconciliationKind: 'push_rejected' })
    await s.approve()
    expect(s.state.pushFailure).toBeNull()
    expect(s.state.review?.fingerprint).toBe('fp-2')
    expect(s.groupCard.value).toBe('review')
  })

  it('an apply failure shows only the error count', async () => {
    const { fake, s } = await reviewSession()
    fake.source.approveReview.mockResolvedValueOnce({ status: 'pre_commit_validation_failed', reviewState: 'resolved_pending_review', reconciliationKind: null, errorCount: 3 })
    await s.approve()
    expect(s.state.notices.review).toEqual({ code: 'apply_failed', params: { n: 3 } })
  })

  it('stale_review re-reads the review and says the target changed', async () => {
    const { fake, s } = await reviewSession()
    fake.source.approveReview.mockRejectedValueOnce(gitError(409, 'stale_review'))
    const reads = fake.source.fetchReview.mock.calls.length
    await s.approve()
    expect(s.state.notices.review).toEqual({ code: 'stale_review' })
    expect(fake.source.fetchReview.mock.calls.length).toBe(reads + 1)
  })

  it('does not approve without can_approve', async () => {
    const { fake, s } = await reviewSession({ review: review({ canApprove: false }) })
    await s.approve()
    expect(fake.source.approveReview).not.toHaveBeenCalled()
  })

  it('reject sends the trimmed reason and the pinned default AI, then the AI card follows', async () => {
    const { fake, s } = await reviewSession()
    s.openReject(true)
    s.setRejectReason('  테스트를 지우면 안 됨  ')
    fake.db.ai = { active: true, runId: 'run-2', status: 'running', actionScope: 'resolve_conflict' }
    await s.reject()
    expect(fake.source.rejectReview).toHaveBeenCalledWith(G, 7, '테스트를 지우면 안 됨', 'opus')
    expect(s.state.rejectOpen).toBe(false)
    expect(s.groupCard.value).toBe('ai')
  })

  it('reject validates the reason length on the client too', async () => {
    const { fake, s } = await reviewSession()
    s.setRejectReason('   ')
    await s.reject()
    s.setRejectReason('x'.repeat(4001))
    await s.reject()
    expect(fake.source.rejectReview).not.toHaveBeenCalled()
    expect(s.state.notices.review).toEqual({ code: 'invalid_reason' })
  })

  it.each([
    ['no default', { providerIds: ['opus'], defaultProviderId: null }],
    ['a default outside the list', { providerIds: ['opus'], defaultProviderId: 'gone' }],
  ])('has no reject AI with %s (never the first one)', async (_label, providers) => {
    const { fake, s } = await reviewSession({ providers })
    expect(s.defaultProviderId.value).toBeNull()
    s.setRejectReason('reason')
    await s.reject()
    expect(fake.source.rejectReview).not.toHaveBeenCalled()
  })

  it('polls while the review is applying', async () => {
    const clock = manualSleep()
    const fake = createFakeGitSource({
      status: status({ pending: [row({ reviewState: 'applying' })] }),
      review: review({ reviewState: 'applying', canApprove: false, canReject: false }),
    })
    await open(fake, { sleep: clock.sleep })
    expect(clock.pending).toBe(1)
  })

  it('keeps polling a long reconciling past 20 minutes until it ends', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ reviewState: 'reconciling' })] }),
      review: review({ reviewState: 'reconciling', canApprove: false, canReject: false }),
    })
    let naps = 0
    const sleep = vi.fn(async () => {
      naps += 1
      if (naps === 700) {
        fake.db.status = status({ pending: [row({ reviewState: 'resolved_pending_review' })] })
        fake.db.review = review()
      }
    })
    const s = await open(fake, { sleep })
    await flushPromises()
    expect(naps).toBe(700)
    expect(s.groupCard.value).toBe('review')
    expect(s.state.review?.reviewState).toBe('resolved_pending_review')
  })
})

describe('apply on hold (Q 395)', () => {
  it('stage 1: method unknown, apply blocked, project default never used', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ status: 'waiting', mergeId: null })] }) })
    const s = await open(fake)
    expect(s.holdView.value).toMatchObject({ stage: 1, action: null, targetBranch: 'v0.2', blockReason: 'stage1' })
    await s.applyNow()
    expect(fake.source.finalize).not.toHaveBeenCalled()
    expect(s.badge.value).toEqual({ tone: 'count', text: '1' })
  })

  it('stage 2 without a plan: undecided and blocked', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: null, targetBranch: null } })] }),
    })
    const s = await open(fake)
    expect(s.holdView.value).toMatchObject({ stage: 2, action: null, blockReason: 'no_plan' })
  })

  it('stage 2 with a plan sends the planned action and target explicitly', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    const s = await open(fake)
    expect(s.holdView.value).toMatchObject({ stage: 2, action: 'merge', targetBranch: 'main', blockReason: null })
    await s.applyNow()
    expect(fake.source.finalize).toHaveBeenCalledWith(G, 'merge', 'main')
  })

  it('stage 2 push sends no target branch', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'push', targetBranch: 'main' } })] }),
    })
    const s = await open(fake)
    await s.applyNow()
    expect(fake.source.finalize).toHaveBeenCalledWith(G, 'push', null)
  })

  it('final_approval_bound keeps apply off even with a plan', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, finalApprovalBound: true, plan: { action: 'merge', targetBranch: null } })] }),
    })
    const s = await open(fake)
    expect(s.holdView.value?.blockReason).toBe('final_approval_bound')
  })
})

describe('remote apply failure (G1)', () => {
  async function failing() {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    fake.source.finalize.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    const s = await open(fake)
    await s.applyNow()
    return { fake, s }
  }

  it('push_rejected records the request, code, status and time', async () => {
    const { s } = await failing()
    expect(s.state.pushFailure).toEqual({
      request: { kind: 'finalize', groupId: G, action: 'merge', targetBranch: 'main' },
      code: 'push_rejected',
      httpStatus: 500,
      at: '2026-10-07T01:02:03.000Z',
      target: 'main',
    })
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
  })

  it('다시 반영 resends the recorded request as it was, even after the plan changed', async () => {
    const { fake, s } = await failing()
    fake.db.status = status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'push', targetBranch: null } })] })
    await s.refresh()
    await s.retryApply()
    expect(fake.source.finalize).toHaveBeenLastCalledWith(G, 'merge', 'main')
    expect(s.state.pushFailure).toBeNull()
  })

  it('another push_rejected keeps the record with a new time; another error keeps it with a notice', async () => {
    const { fake, s } = await failing()
    fake.source.finalize.mockRejectedValueOnce(gitError(409, 'git_busy'))
    await s.retryApply()
    expect(s.state.pushFailure?.code).toBe('push_rejected')
    expect(s.state.notices.push).toEqual({ code: 'git_busy' })
  })

  it('the record is project scoped: a Flow switch keeps it, a project switch drops it', async () => {
    const { s } = await failing()
    s.setContext(P, { groupId: 'p.m.0002', name: 'other' })
    await flushPromises()
    expect(s.state.pushFailure).not.toBeNull()
    s.setContext('q', null)
    await flushPromises()
    expect(s.state.pushFailure).toBeNull()
  })
})

describe('uncommitted Base changes (Q 396)', () => {
  it('counts tracked + new files exactly and commits both lists', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts', 'b.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['n.md'], truncated: false, total: null },
      }),
    })
    const s = await open(fake)
    expect(s.dirtyView.value).toMatchObject({ stage: 1, count: 3, blockReason: null })
    expect(s.badge.value).toEqual({ tone: 'count', text: '3' })
    s.setCommitMessage('fix: base')
    fake.db.status = status()
    await s.commit()
    expect(fake.source.baseCommit).toHaveBeenCalledWith(P, 'fix: base', ['a.ts', 'b.ts', 'n.md'])
    expect(s.state.commitMessage).toBe('')
    expect(s.dirtyView.value).toBeNull()
  })

  it('revert all reverts tracked then removes new files', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['n.md'], truncated: false, total: null },
      }),
    })
    const s = await open(fake)
    await s.revertAll()
    expect(fake.source.baseRevert).toHaveBeenCalledWith(P, ['a.ts'])
    expect(fake.source.baseRemove).toHaveBeenCalledWith(P, ['n.md'])
  })

  it('a failed revert request stops before anything is deleted; partial file errors are counted', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['n.md'], truncated: false, total: null },
      }),
    })
    fake.source.baseRevert.mockRejectedValueOnce(gitError(409, 'invalid_state'))
    const s = await open(fake)
    await s.revertAll()
    expect(fake.source.baseRemove).not.toHaveBeenCalled()
    fake.source.baseRevert.mockResolvedValueOnce({ results: [{ path: 'a.ts', result: 'error' }], remaining: ['a.ts'] })
    await s.revertAll()
    expect(s.state.notices.dirty).toEqual({ code: 'partial_failed', params: { n: 1 } })
  })

  it('does not send an empty list', async () => {
    const fake = createFakeGitSource({
      status: status({ baseUntracked: { files: ['n.md'], truncated: false, total: null } }),
    })
    const s = await open(fake)
    await s.revertAll()
    expect(fake.source.baseRevert).not.toHaveBeenCalled()
    expect(fake.source.baseRemove).toHaveBeenCalledWith(P, ['n.md'])
  })

  it('stage 1 truncated: split count, "+" badge, commit / revert blocked, nothing sent', async () => {
    const untracked = Array.from({ length: 200 }, (_, i) => `gen/${i}.txt`)
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts', 'b.ts', 'c.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: untracked, truncated: true, total: null },
      }),
    })
    const s = await open(fake)
    expect(s.dirtyView.value).toMatchObject({ stage: 1, count: null, truncated: true, blockReason: 'truncated' })
    expect(s.badge.value).toEqual({ tone: 'count', text: '203+' })
    await s.commit()
    await s.revertAll()
    expect(fake.source.baseCommit).not.toHaveBeenCalled()
    expect(fake.source.baseRevert).not.toHaveBeenCalled()
    expect(fake.source.baseRemove).not.toHaveBeenCalled()
  })

  it('stage 2 truncated with the S2 source: exact total and the all-scope requests with the shown total', async () => {
    const untracked = Array.from({ length: 200 }, (_, i) => `gen/${i}.txt`)
    const baseCommitAll = async () => ({ committed: true, remaining: [], remainingUntracked: [] })
    const baseRevertAll = async () => ({ results: [], remaining: [], remainingUntracked: [] })
    const fake = createFakeGitSource(
      {
        status: status({
          baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false },
          baseUntracked: { files: untracked, truncated: true, total: 250 },
        }),
      },
      { baseCommitAll: vi.fn(baseCommitAll), baseRevertAll: vi.fn(baseRevertAll) },
    )
    const s = await open(fake)
    expect(s.dirtyView.value).toMatchObject({ stage: 2, count: 251, hiddenUntracked: 50, blockReason: null })
    expect(s.badge.value).toEqual({ tone: 'count', text: '251' })
    await s.commit()
    expect(fake.source.baseCommitAll).toHaveBeenCalledWith(P, '', 250)
    await s.revertAll()
    // One request for tracked + every new file; the stage-1 pair is not used.
    expect(fake.source.baseRevertAll).toHaveBeenCalledWith(P, ['a.ts'], 250)
    expect(fake.source.baseRevert).not.toHaveBeenCalled()
    expect(fake.source.baseRemove).not.toHaveBeenCalled()
  })

  it('stage 2: a total mismatch on revert-all changes nothing, tracked files included', async () => {
    const fake = createFakeGitSource(
      {
        status: status({
          baseDirty: { files: ['a.ts', 'b.ts'], mergeInProgress: false, aiRun: false },
          baseUntracked: { files: ['n.md'], truncated: true, total: 300 },
        }),
      },
      {
        baseCommitAll: vi.fn(async () => ({ committed: true, remaining: [], remainingUntracked: [] })),
        baseRevertAll: vi.fn(async () => {
          throw gitError(409, 'untracked_total_mismatch')
        }),
      },
    )
    const s = await open(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    await s.revertAll()
    expect(fake.source.baseRevertAll).toHaveBeenCalledTimes(1)
    expect(fake.source.baseRevertAll).toHaveBeenCalledWith(P, ['a.ts', 'b.ts'], 300)
    // No tracked revert went out before (or after) the refused all-scope request.
    expect(fake.source.baseRevert).not.toHaveBeenCalled()
    expect(fake.source.baseRemove).not.toHaveBeenCalled()
    expect(s.state.notices.dirty).toEqual({ code: 'untracked_changed' })
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })

  it('stage 2: a total mismatch refusal re-reads and asks again', async () => {
    const fake = createFakeGitSource(
      {
        status: status({ baseUntracked: { files: ['n.md'], truncated: true, total: 300 } }),
      },
      {
        baseCommitAll: vi.fn(async () => {
          throw gitError(409, 'untracked_total_mismatch')
        }),
        baseRevertAll: vi.fn(async () => ({ results: [], remaining: [], remainingUntracked: [] })),
      },
    )
    const s = await open(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    await s.commit()
    expect(s.state.notices.dirty).toEqual({ code: 'untracked_changed' })
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })

  it('S2 total without S2 actions in the source stays blocked (no invented request)', async () => {
    const fake = createFakeGitSource({ status: status({ baseUntracked: { files: ['n.md'], truncated: true, total: 300 } }) })
    const s = await open(fake)
    expect(s.dirtyView.value).toMatchObject({ stage: 2, count: 300, blockReason: 'truncated' })
  })

  it('merge in progress hides the card; a Base AI run keeps it but blocks actions', async () => {
    const fake = createFakeGitSource({ status: status({ baseDirty: { files: ['a.ts'], mergeInProgress: true, aiRun: false } }) })
    const s = await open(fake)
    expect(s.dirtyView.value).toBeNull()
    fake.db.status = status({ baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: true } })
    await s.refresh()
    expect(s.dirtyView.value?.blockReason).toBe('ai_run')
  })

  it('tracked letters come from the Base diff, once per status generation, unknown on failure', async () => {
    const fake = createFakeGitSource({
      status: status({ baseDirty: { files: ['a.ts', '.env'], mergeInProgress: false, aiRun: false } }),
      fileLetters: { 'a.ts': 'A' },
    })
    const s = await open(fake)
    s.ensureFileStatus(['a.ts', '.env'])
    s.ensureFileStatus(['a.ts'])
    await flushPromises()
    expect(fake.source.loadBaseFileDiff).toHaveBeenCalledTimes(2)
    expect(s.state.fileStatus).toEqual({ 'a.ts': 'A', '.env': null })
    expect(s.state.notices.dirty).toBeUndefined()
    await s.refresh()
    expect(s.state.fileStatus).toEqual({})
  })

  it('readOnly blocks every action', async () => {
    const fake = createFakeGitSource({ status: status({ baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false } }) })
    const s = await open(fake)
    s.setReadOnly(true)
    await s.commit()
    expect(fake.source.baseCommit).not.toHaveBeenCalled()
  })
})

describe('badge priority and context', () => {
  it('danger > progress > count', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row()], baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false } }),
    })
    const s = await open(fake)
    expect(s.badge.value).toEqual({ tone: 'danger', text: '!' })
  })

  it('hold + dirty add up', async () => {
    const fake = createFakeGitSource({
      status: status({
        pending: [row({ status: 'waiting', mergeId: null })],
        baseDirty: { files: ['a.ts', 'b.ts', 'c.ts'], mergeInProgress: false, aiRun: false },
      }),
    })
    const s = await open(fake)
    expect(s.badge.value).toEqual({ tone: 'count', text: '4' })
  })

  it('a late answer of the previous project does not overwrite the new one', async () => {
    let release: (value: ReturnType<typeof status>) => void = () => undefined
    const fake = createFakeGitSource()
    fake.source.loadStatus.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
    session = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
    session.setContext(P, { groupId: G, name: 'f' })
    session.setContext('q', null)
    await flushPromises()
    release(status({ baseBranch: 'stale' }))
    await flushPromises()
    expect(session.state.projectId).toBe('q')
    expect(session.state.status?.baseBranch).toBe('v0.2')
  })

  it('a Flow switch closes the other group conflict window and keeps project windows', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    s.openConflictWindow('충돌 해결')
    expect(s.state.windows.map((w) => w.id)).toEqual([`conflict:${G}:7`])
    s.setContext(P, { groupId: 'p.m.0002', name: 'other' })
    await flushPromises()
    expect(s.state.windows).toEqual([])
  })
})

describe('windows (§6.4)', () => {
  it('opens, minimizes, restores and closes; reopening restores the same window', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const s = await open(fake)
    const reads = fake.source.fetchConflicts.mock.calls.length
    s.openConflictWindow('충돌 해결')
    expect(fake.source.fetchConflicts.mock.calls.length).toBe(reads + 1)
    const id = s.state.windows[0].id
    s.setWindowMinimized(id, true)
    expect(s.state.windows[0].minimized).toBe(true)
    s.openConflictWindow('충돌 해결')
    expect(s.state.windows).toHaveLength(1)
    expect(s.state.windows[0].minimized).toBe(false)
    s.setWindowMinimized(id, true)
    s.restoreWindow(id)
    expect(s.state.windows[0].minimized).toBe(false)
    s.closeWindow(id)
    expect(s.state.windows).toEqual([])
  })

  it('the conflict window goes away with the conflict', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), conflicts: conflicts(2) })
    const s = await open(fake)
    s.openConflictWindow('충돌 해결')
    fake.db.status = status()
    await s.refresh()
    expect(s.state.windows).toEqual([])
  })
})
