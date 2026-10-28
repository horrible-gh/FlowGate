import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import type { FlowWorkBase, ProjectGitStatus } from '@/features/project'
import type { GitSource } from '@/features/git'
import { createGitSession, type GitSession } from '@/features/git/gitSession'
import { createGitWorktreeSession, type GitWorktreeSession } from '@/features/git/gitWorktreeSession'
import type { GitWorktreeSource } from '@/features/git/gitSource'
import { G, P, createFakeGitSource, gitError, manualSleep, networkError, status } from './fakeGitSource'
import { createFakeWorktreeSource, flowWorkBase } from './fakeWorktreeSource'

// flowgate.v02.0107 T#1 — the Group worktree session as the one owner of the active Flow's work
// base (D §3.2, §3.3, §6.2-5 / 6 / 8): when it is read and when not, what an answer must name to
// apply, what a stale / failed / mismatched answer leaves, the shared running read, and that a
// re-read leaves E1 / E8 and confirm tickets alone. Fixtures keep two different bases apart: the
// project base is `main`, Flow A works on `feature/v0.2.0`, Flow B on `main`.

const FLOW_A = { groupId: G, name: 'A' }
const G2 = 'p.m.0002'
const FLOW_B = { groupId: G2, name: 'B' }
const P2 = 'p2'
const G3 = 'p2.m.0001'

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

// Flow A answers `feature/v0.2.0`, Flow B `main`, the Flow of project p2 `release/p2`.
const answerOf = (groupId: string): FlowWorkBase => {
  if (groupId === G) return flowWorkBase(G, { workBaseRef: 'feature/v0.2.0', effectiveWorkBaseRef: 'feature/v0.2.0' })
  if (groupId === G2) return flowWorkBase(G2, { workBaseRef: 'main', effectiveWorkBaseRef: 'main' })
  return flowWorkBase(groupId, { projectId: P2, workBaseRef: 'release/p2', effectiveWorkBaseRef: 'release/p2' })
}

function setup(init: { gitStatus?: ProjectGitStatus | null; gitExtra?: Partial<GitSource>; wtExtra?: Partial<GitWorktreeSource> } = {}) {
  const wt = createFakeWorktreeSource({}, init.wtExtra ?? {})
  wt.source.loadWorkBase.mockImplementation(async (groupId: string) => answerOf(groupId))
  const fake = createFakeGitSource({ status: init.gitStatus === undefined ? status({ baseBranch: 'main' }) : init.gitStatus }, {
    worktree: wt.source,
    ...(init.gitExtra ?? {}),
  })
  const git = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
  const session = createGitWorktreeSession({ git, source: fake.source })
  sessions.push({ git, wt: session })
  return { wt, fake, git, session }
}

type T = ReturnType<typeof setup>

const reads = (t: T) => t.wt.source.loadWorkBase.mock.calls.map((call) => call[0])
const shown = (t: T) => ({ value: t.session.state.workBase, load: t.session.state.workBaseLoad })

async function open(t: T, flow: { groupId: string; name: string } | null = FLOW_A, projectId = P) {
  t.git.setContext(projectId, flow)
  await flushPromises()
}

// The next work-base read waits for the test.
function holdNext(t: T) {
  const answer = deferred<FlowWorkBase>()
  t.wt.source.loadWorkBase.mockImplementationOnce(() => answer.promise)
  return answer
}

describe('gitWorktreeSession — Flow work base reads (D §3.3)', () => {
  it('the gate opening reads it once: the effective work base, apart from the project base', async () => {
    const t = setup()
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    await open(t)
    expect(reads(t)).toEqual([G])
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
    expect(t.session.projectGit.value?.baseBranch).toBe('main')
  })

  it('nothing is read before git/status confirms Git on, with Git off or without an active Flow', async () => {
    const answer = deferred<ProjectGitStatus | null>()
    const t = setup()
    t.fake.source.loadStatus.mockImplementationOnce(() => answer.promise)
    await open(t)
    expect(t.session.gateOpen.value).toBe(false)
    expect(reads(t)).toEqual([])
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    answer.resolve(status({ baseBranch: 'main' }))
    await flushPromises()
    expect(reads(t)).toEqual([G])

    const off = setup({ gitStatus: status({ enabled: false, baseBranch: 'main' }) })
    await open(off)
    const noFlow = setup()
    await open(noFlow, null)
    for (const s of [off, noFlow]) {
      expect(reads(s)).toEqual([])
      expect(shown(s)).toEqual({ value: null, load: 'idle' })
    }
  })

  it('while the read runs the value is empty and loading; a legacy null shows the server fallback', async () => {
    const t = setup()
    const answer = holdNext(t)
    await open(t)
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    answer.resolve(flowWorkBase(G, { workBaseRef: null, effectiveWorkBaseRef: 'main' }))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
  })

  it('no effective value at all is an empty value (`—`), never a synthesized `main` or project base', async () => {
    const t = setup()
    t.wt.source.loadWorkBase.mockResolvedValueOnce(flowWorkBase(G, { workBaseRef: null, effectiveWorkBaseRef: null }))
    await open(t)
    expect(shown(t)).toEqual({ value: null, load: 'ready' })
    t.wt.source.loadWorkBase.mockResolvedValueOnce(flowWorkBase(G, { effectiveWorkBaseRef: '  ' }))
    await t.session.refreshWorkBase()
    expect(shown(t)).toEqual({ value: null, load: 'ready' })
  })

  it('E1 / E8 re-reads, the 0033 refresh, render-time reads and pane changes send no work-base GET', async () => {
    const t = setup()
    await open(t)
    const e1 = t.wt.source.loadStatus.mock.calls.length
    t.session.refresh()
    await flushPromises()
    await t.git.refresh()
    await flushPromises()
    t.session.toggleApply()
    t.session.toggleInfo('status')
    t.session.toggleInfo('diff')
    await flushPromises()
    for (let i = 0; i < 3; i += 1) void t.session.state.workBase
    expect(t.wt.source.loadStatus.mock.calls.length).toBeGreaterThan(e1)
    expect(reads(t)).toEqual([G])
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('refreshWorkBase reads again, empties the value until the answer and shares a running read', async () => {
    const t = setup()
    await open(t)
    const answer = holdNext(t)
    const first = t.session.refreshWorkBase()
    const second = t.session.refreshWorkBase()
    expect(second).toBe(first)
    expect(reads(t)).toEqual([G, G])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    answer.resolve(answerOf(G))
    await first
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
    // Done: the next refresh is a new read.
    await t.session.refreshWorkBase()
    expect(reads(t)).toEqual([G, G, G])
  })

  it('the first gate read and a refresh in the same context share one GET', async () => {
    const t = setup()
    const answer = holdNext(t)
    await open(t)
    void t.session.refreshWorkBase()
    void t.session.refreshWorkBase()
    expect(reads(t)).toEqual([G])
    answer.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('reads nothing while the gate is closed', async () => {
    const t = setup()
    await open(t, null)
    await t.session.refreshWorkBase()
    expect(reads(t)).toEqual([])
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
  })
})

describe('gitWorktreeSession — Flow work base races (D §3.2, §6.2-5)', () => {
  it('a slow A answer after B was chosen applies nowhere', async () => {
    const t = setup()
    const slowA = holdNext(t)
    await open(t, FLOW_A)
    await open(t, FLOW_B)
    expect(reads(t)).toEqual([G, G2])
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
    slowA.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
  })

  it('a slow A failure after B was chosen changes neither the value nor the load state', async () => {
    const t = setup()
    const slowA = holdNext(t)
    await open(t, FLOW_A)
    const slowB = holdNext(t)
    await open(t, FLOW_B)
    slowA.reject(gitError(404, 'not_found'))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    slowB.resolve(answerOf(G2))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
  })

  it('A → B → A: the first A read is neither shared nor applied', async () => {
    const t = setup()
    const firstA = holdNext(t)
    await open(t, FLOW_A)
    const b = holdNext(t)
    await open(t, FLOW_B)
    const secondA = holdNext(t)
    await open(t, FLOW_A)
    void t.session.refreshWorkBase()
    expect(reads(t)).toEqual([G, G2, G])
    firstA.resolve(flowWorkBase(G, { effectiveWorkBaseRef: 'stale-a' }))
    b.resolve(answerOf(G2))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    secondA.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('a project switch empties the value before the new answer; the old project answer is ignored', async () => {
    const t = setup()
    await open(t, FLOW_A)
    const oldRead = holdNext(t)
    void t.session.refreshWorkBase()
    const p2 = holdNext(t)
    await open(t, { groupId: G3, name: 'P2 flow' }, P2)
    expect(reads(t)).toEqual([G, G, G3])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    oldRead.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    p2.resolve(answerOf(G3))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'release/p2', load: 'ready' })
  })

  it('removing the Flow empties the value and drops the running read; no read while there is none', async () => {
    const t = setup()
    await open(t)
    const running = holdNext(t)
    void t.session.refreshWorkBase()
    await open(t, null)
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    running.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    expect(reads(t)).toEqual([G, G])
    // Choosing it again is a new read.
    await open(t, FLOW_A)
    expect(reads(t)).toEqual([G, G, G])
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('a closed gate empties the value and drops the running read; the reopened gate reads again', async () => {
    const t = setup()
    await open(t)
    const running = holdNext(t)
    void t.session.refreshWorkBase()
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await t.git.refresh()
    await flushPromises()
    expect(t.session.gateOpen.value).toBe(false)
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    running.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    await t.git.refresh()
    await flushPromises()
    expect(t.session.gateOpen.value).toBe(true)
    expect(reads(t)).toEqual([G, G, G])
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('dispose empties the value and the running answer applies nowhere', async () => {
    const t = setup()
    await open(t)
    const running = holdNext(t)
    void t.session.refreshWorkBase()
    t.session.dispose()
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    running.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    await t.session.refreshWorkBase()
    expect(reads(t)).toEqual([G, G])
  })
})

// The context is changed several times with no tick, nextTick or flushPromises in between: the
// batched context watcher runs once and sees only the last context, the work base must not.
describe('gitWorktreeSession — Flow work base, switches within one turn (D §3.2, §3.3)', () => {
  it('A → B → A and a refresh in one turn: the first A read is neither shared nor applied', async () => {
    const t = setup()
    const firstA = holdNext(t)
    await open(t, FLOW_A)
    const running = t.session.refreshWorkBase()
    const secondA = holdNext(t)
    t.git.setContext(P, FLOW_B)
    t.git.setContext(P, FLOW_A)
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    const again = t.session.refreshWorkBase()
    expect(again).not.toBe(running)
    expect(reads(t)).toEqual([G, G])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    firstA.resolve(flowWorkBase(G, { effectiveWorkBaseRef: 'stale-a' }))
    await flushPromises()
    // No B read, no second gate read for A.
    expect(reads(t)).toEqual([G, G])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    secondA.resolve(answerOf(G))
    await again
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('A → B → A in one turn without a refresh: the gate reads the last A once; the old answer applies nowhere', async () => {
    const t = setup()
    const firstA = holdNext(t)
    await open(t, FLOW_A)
    const secondA = holdNext(t)
    t.git.setContext(P, FLOW_B)
    t.git.setContext(P, FLOW_A)
    firstA.resolve(flowWorkBase(G, { effectiveWorkBaseRef: 'stale-a' }))
    await flushPromises()
    expect(reads(t)).toEqual([G, G])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    secondA.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
  })

  it('a refresh right after A → B (before any tick) reads B once — one GET, shared, applied', async () => {
    const t = setup()
    await open(t, FLOW_A)
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
    const b = holdNext(t)
    t.git.setContext(P, FLOW_B)
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    const first = t.session.refreshWorkBase()
    const second = t.session.refreshWorkBase()
    expect(second).toBe(first)
    expect(reads(t)).toEqual([G, G2])
    await flushPromises()
    expect(reads(t)).toEqual([G, G2])
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    b.resolve(answerOf(G2))
    await flushPromises()
    expect(reads(t)).toEqual([G, G2])
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
    // The batched context watcher still read E1 for B once the turn ended.
    expect(t.wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G2)
  })

  it('a slow A answer after A → B and a refresh in the same turn applies nowhere', async () => {
    const t = setup()
    const slowA = holdNext(t)
    await open(t, FLOW_A)
    const b = holdNext(t)
    t.git.setContext(P, FLOW_B)
    void t.session.refreshWorkBase()
    slowA.resolve(answerOf(G))
    await flushPromises()
    expect(shown(t)).toEqual({ value: null, load: 'loading' })
    b.resolve(answerOf(G2))
    await flushPromises()
    expect(reads(t)).toEqual([G, G2])
    expect(shown(t)).toEqual({ value: 'main', load: 'ready' })
  })

  it('removing the Flow empties the value at once, before any tick, and reads nothing', async () => {
    const t = setup()
    await open(t, FLOW_A)
    const running = holdNext(t)
    void t.session.refreshWorkBase()
    t.git.setContext(P, null)
    expect(t.session.gateOpen.value).toBe(false)
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
    await t.session.refreshWorkBase()
    running.resolve(answerOf(G))
    await flushPromises()
    expect(reads(t)).toEqual([G, G])
    expect(shown(t)).toEqual({ value: null, load: 'idle' })
  })

  it('switches within one turn keep the confirm ticket contract and the E1 / E8 reads of the last context', async () => {
    const t = setup()
    await open(t, FLOW_A)
    const held = t.session.ticket()
    expect(held).not.toBeNull()
    t.git.setContext(P, FLOW_B)
    expect(t.session.isCurrent(held!)).toBe(false)
    void t.session.refreshWorkBase()
    await flushPromises()
    expect(t.session.isCurrent(held!)).toBe(false)
    expect(t.session.ticket()?.groupId).toBe(G2)
    expect(t.wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G2)
    expect(t.wt.source.loadManualMerge.mock.calls.at(-1)?.[0]).toBe(G2)
    expect(reads(t)).toEqual([G, G2])
  })
})

describe('gitWorktreeSession — Flow work base answers (D §3.2, §6.2-5 / 6)', () => {
  it.each([
    ['another project', flowWorkBase(G, { projectId: 'other', effectiveWorkBaseRef: 'feature/x' })],
    ['another Flow', flowWorkBase(G2, { effectiveWorkBaseRef: 'feature/x' })],
    ['a malformed ref', flowWorkBase(G, { effectiveWorkBaseRef: 7 as unknown as string })],
    ['no answer', null as unknown as FlowWorkBase],
  ])('%s is refused: no value, a failed read', async (_name, answer) => {
    const t = setup()
    await open(t)
    t.wt.source.loadWorkBase.mockResolvedValueOnce(answer)
    await t.session.refreshWorkBase()
    expect(shown(t)).toEqual({ value: null, load: 'error' })
  })

  it.each([
    ['403', () => gitError(403, 'forbidden')],
    ['404', () => gitError(404, 'not_found')],
    ['network', () => networkError()],
  ])('a %s failure empties the value — not the last one, not the project base — and is not retried', async (_name, error) => {
    const t = setup()
    await open(t)
    expect(shown(t)).toEqual({ value: 'feature/v0.2.0', load: 'ready' })
    t.wt.source.loadWorkBase.mockRejectedValueOnce(error())
    await t.session.refreshWorkBase()
    expect(shown(t)).toEqual({ value: null, load: 'error' })
    expect(t.session.projectGit.value?.baseBranch).toBe('main')
    // A failed display read decides nothing about worktree writes.
    expect(t.session.state.forbidden).toBe(false)
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(reads(t)).toEqual([G, G])
  })

  it('a Git-off answer decides nothing about the gate or the project Git state', async () => {
    const t = setup()
    t.wt.source.loadWorkBase.mockResolvedValueOnce(flowWorkBase(G, { gitEnabled: false, effectiveWorkBaseRef: null }))
    await open(t)
    expect(t.session.gateOpen.value).toBe(true)
    expect(t.git.state.status?.enabled).toBe(true)
    expect(shown(t)).toEqual({ value: null, load: 'ready' })
  })

  it('a re-read leaves the E1 order, the E8 target and an open confirm ticket as they are', async () => {
    const t = setup()
    await open(t)
    const held = t.session.ticket()
    expect(held).not.toBeNull()
    const snapshot = () => ({
      e1: t.wt.source.loadStatus.mock.calls.length,
      e8: t.wt.source.loadManualMerge.mock.calls.length,
      e10: t.wt.source.loadMergeResults.mock.calls.length,
      mergeTarget: t.session.state.mergeTarget,
      mergeSeq: t.session.state.mergeSeq,
      worktreeToken: t.session.state.status?.worktreeToken,
    })
    const before = snapshot()
    await t.session.refreshWorkBase()
    t.wt.source.loadWorkBase.mockRejectedValueOnce(gitError(404, 'not_found'))
    await t.session.refreshWorkBase()
    await flushPromises()
    expect(t.session.isCurrent(held!)).toBe(true)
    expect(snapshot()).toEqual(before)
  })
})
