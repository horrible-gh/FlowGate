import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS, type ChatRunState } from '@/features/conversation/api'
import {
  FINAL_ACTIVITY_READ_ATTEMPTS,
  FINAL_TURN_READ_ATTEMPTS,
  RUN_POLL_MAX,
  RUN_POLL_MS,
  createConversationSession,
  type ConversationSession,
} from '@/features/conversation/conversationSession'
import type {
  ActivityAnchor,
  CommandRequestActivity,
  ConversationActivity,
  ConversationTurn,
  TurnsPage,
  WorkChangeActivity,
} from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0151 T-A2 (NR Rev.4 §2.6-2, §2.6-3, §2.8): a run the polling would give up on is checked against the
// server's active run first, a catch-up re-reads the activities, and the final activity read is retried. The fake
// source plays the server and Vitest's fake timers move RUN_POLL_MS / RUN_POLL_MAX; nothing reaches a real server.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
const LONG = 60_000 // the RUN_POLL_MAX walks fire 480 fake timers, each followed by a real event-loop turn

const FINISHED: ChatRunState = { runId: 'r1', docRef: A, actionScope: 'chat', groupId: SCOPE.groupId,
  status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0, succeeded: true }
const RUNNING: ChatRunState = { ...FINISHED, status: 'running', endReason: null, outcome: null, exitCode: null, succeeded: null }
const FAILED: ChatRunState = { ...FINISHED, endReason: 'worker_error', outcome: 'none', exitCode: null, succeeded: false }
const CANCELLED: ChatRunState = { ...FINISHED, endReason: 'cancelled', outcome: 'none', exitCode: null, succeeded: false }

const sessions: ConversationSession[] = []
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
})
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
  vi.useRealTimers()
})

/** Moves the fake clock by `n` polling intervals and lets every answer land. */
async function poll(n = 1): Promise<void> {
  await vi.advanceTimersByTimeAsync(RUN_POLL_MS * n)
  await flushPromises()
}

function page(turns: ConversationTurn[] = []): TurnsPage {
  return { turns, headSeq: Math.max(0, ...turns.map((t) => t.seq)), nextAfterSeq: null, prevBeforeSeq: null, participants: [], myReadSeq: null }
}

const after = (seq: number, state: ActivityAnchor['state'] = 'reply'): ActivityAnchor => ({ seq, position: 'after', state })
const unplaced = (state: ActivityAnchor['state']): ActivityAnchor => ({ seq: null, position: null, state })

function change(runId: string, anchor: ActivityAnchor, paths: string[] = ['src/a.ts'], createdAt: string | null = null): WorkChangeActivity {
  return { kind: 'change', id: runId, runId, createdAt, anchor, filesChanged: paths.length, insertions: paths.length, deletions: 0,
    files: paths.map((path) => ({ path, status: 'M', insertions: 1, deletions: 0 })) }
}

/** A command card without a run, so the run-history restore has nothing to judge for it. */
function command(id: string, anchor: ActivityAnchor, state: CommandRequestActivity['state'] = 'pending_approval'): CommandRequestActivity {
  return { kind: 'command', id, runId: null, createdAt: null, command: `git ${id}`, state, anchor }
}

const codes = (s: ConversationSession) => s.state.activities.filter((a) => a.kind === 'status').map((a) => a.code)
const placed = (s: ConversationSession) => s.timeline.value.filter((item) => item.kind !== 'status').map((item) => item.key)
const cards = (s: ConversationSession) => s.state.activities.filter((a): a is WorkChangeActivity => a.kind === 'change')
const commandState = (s: ConversationSession, id: string) =>
  s.state.activities.find((a): a is CommandRequestActivity => a.kind === 'command' && a.id === id)?.state
const readsOf = (fn: { mock: { calls: unknown[][] } }, chatId: string) => fn.mock.calls.filter((call) => call[0] === chatId).length

async function open(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'start')], [B]: [turn(1, 'user', 'other chat')] },
    ...init,
  })
  // Every run the restore or a poll asks about is this chat's normal finished run unless a test says otherwise.
  fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...FINISHED, runId: id }))
  const session = createConversationSession({ source: fake.source })
  sessions.push(session)
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session }
}

/** fetchRun answers from `details` (a value, or an error to throw); other runs are normal finished runs. */
function serve(source: { fetchRun: ReturnType<typeof vi.fn> }, details: Record<string, () => ChatRunState | Error>) {
  source.fetchRun.mockImplementation(async (id: string) => {
    const answer = details[id]?.() ?? { ...FINISHED, runId: id }
    if (answer instanceof Error) throw answer
    return { ...answer, runId: id }
  })
}

describe('0151 T-A2 §2.6-2: the server active run decides before the polling gives a run up', () => {
  it('V3: past RUN_POLL_MAX the lock stays while the server lists the run as active, then the finish is judged', async () => {
    const { session, source, data } = await open()
    let detail = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    data.activeRun = 'r1'
    const activeBefore = source.fetchActiveChatRun.mock.calls.length

    await poll(RUN_POLL_MAX)
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX)
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + 1)
    expect(codes(session)).toEqual([])
    expect(session.state.runId).toBe('r1')
    expect(session.running.value).toBe(true)

    await poll(5) // the polling goes on at the same interval
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX + 5)
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])

    data.turns[A].push(turn(2, 'ai', 'long answer', null, { sourceRunId: 'r1' }))
    data.activities[A] = [change('r1', after(2))]
    data.activeRun = null
    detail = FINISHED
    const reads = source.listActivities.mock.calls.length
    await poll()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(source.listActivities.mock.calls.length).toBeGreaterThan(reads)
    expect(placed(session)).toEqual(['turn:1', 'turn:2', 'change:r1'])
  }, LONG)

  it.each([
    ['success', FINISHED, true, [] as string[]],
    ['failure', FAILED, false, ['run_no_reply']],
  ])('V3b: past RUN_POLL_MAX with no active run, one more read judges the real %s', async (_name, finished, withBody, expected) => {
    const { session, source, data } = await open()
    let detail = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()

    await poll(RUN_POLL_MAX)
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX)
    // The server lists no active run; the run is not given up before one more detail read.
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])

    if (withBody) data.turns[A].push(turn(2, 'ai', 'answer', null, { sourceRunId: 'r1' }))
    data.activities[A] = [change('r1', withBody ? after(2) : after(1, 'run_start'))]
    detail = finished
    const reads = source.listActivities.mock.calls.length
    await poll()
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX + 1)
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual(expected)
    expect(codes(session)).not.toContain('run_result_unconfirmed')
    expect(source.listActivities.mock.calls.length).toBeGreaterThanOrEqual(reads + 1)
    expect(cards(session).map((c) => c.runId)).toEqual(['r1'])
  }, LONG)

  it('V3b: a run the server no longer lists and still not terminal on the last read is given up with one line', async () => {
    const { session, source } = await open()
    serve(source, { r1: () => RUNNING })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    const activeBefore = source.fetchActiveChatRun.mock.calls.length
    await poll(RUN_POLL_MAX + 1)
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX + 1)
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + 1)
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual(['run_result_unconfirmed'])
    await poll(3)
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_POLL_MAX + 1)
    expect(codes(session)).toEqual(['run_result_unconfirmed'])
  }, LONG)

  it('V3: an unanswered active query keeps the lock and is asked again, FINAL_TURN_READ_ATTEMPTS times at most', async () => {
    const { session, source } = await open()
    serve(source, { r1: () => RUNNING })
    source.startChatRun.mockResolvedValue('r1')
    source.fetchActiveChatRun.mockRejectedValue(new Error('isolated active query unavailable'))
    await session.invokeAi()
    const activeBefore = source.fetchActiveChatRun.mock.calls.length
    await poll(RUN_POLL_MAX)
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + 1)
    for (let asked = 2; asked <= FINAL_TURN_READ_ATTEMPTS; asked++) {
      expect(session.state.runId).toBe('r1')
      await poll()
      expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + asked)
    }
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])
    await poll() // the one more detail read, still running: given up
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + FINAL_TURN_READ_ATTEMPTS)
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual(['run_result_unconfirmed'])
  }, LONG)

  it.each([true, false])('V3c: a 404 after the run was read asks the active run first (still active=%s)', async (active) => {
    const { session, source, data } = await open()
    let detail: ChatRunState | Error = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    await poll() // read once: running
    detail = httpError(404, 'isolated run gone')
    data.activeRun = active ? 'r1' : null
    const activeBefore = source.fetchActiveChatRun.mock.calls.length
    await poll()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + 1)
    if (active) {
      expect(session.state.runId).toBe('r1')
      expect(codes(session)).toEqual([])
      await poll()
      expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeBefore + 2)
      expect(session.state.runId).toBe('r1')
      data.turns[A].push(turn(2, 'ai', 'answer', null, { sourceRunId: 'r1' }))
      data.activeRun = null
      detail = FINISHED
      await poll()
      expect(session.state.runId).toBeNull()
      expect(codes(session)).toEqual([])
    } else {
      expect(session.state.runId).toBeNull()
      expect(codes(session)).toEqual(['run_result_unconfirmed'])
      await poll(3)
      expect(source.fetchRun).toHaveBeenCalledTimes(2)
      expect(codes(session)).toEqual(['run_result_unconfirmed'])
    }
  })

  it('V3c: a detail that does not prove this chat is not applied and keeps the lock while the run is active', async () => {
    const { session, source, data } = await open()
    let detail = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    detail = { ...RUNNING, docRef: null, actionScope: null }
    data.activeRun = 'r1'
    source.fetchTurns.mockClear()
    await poll()
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])
    expect(source.fetchTurns).not.toHaveBeenCalled()
    data.activeRun = null
    await poll()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual(['run_result_unconfirmed'])
    expect(source.fetchTurns).not.toHaveBeenCalled()
  })
})

describe('0151 T-A2 §2.6-3: catch-up reads turns, activities, then history', () => {
  it('V7b: a failed first activity read shows no card and no error; the same chat selected again repairs it', async () => {
    const fake = createFakeSource({ chats: [{ id: A, title: 'A', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'fix it'), turn(2, 'ai', 'fixed', null, { sourceRunId: 'r0' })] },
      activities: { [A]: [change('r0', after(2))] } })
    fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...FINISHED, runId: id }))
    fake.source.listActivities.mockRejectedValueOnce(httpError(500, 'isolated activity read down'))
    const session = createConversationSession({ source: fake.source })
    sessions.push(session)
    await session.setScope(SCOPE)
    await flushPromises()
    expect(fake.source.listActivities).toHaveBeenCalledTimes(1)
    expect(cards(session)).toEqual([])
    expect(session.state.notice).toBeNull()

    const turnCall = fake.source.fetchTurns.mock.calls.length
    const activityCall = fake.source.listActivities.mock.calls.length
    const historyCall = fake.source.listRuns.mock.calls.length
    await session.selectChat(A) // same chat: catch-up
    expect(fake.source.listActivities).toHaveBeenCalledTimes(2)
    expect(placed(session)).toEqual(['turn:1', 'turn:2', 'change:r0'])
    expect(codes(session)).toEqual([])
    const order = (fn: { mock: { invocationCallOrder: number[] } }, at: number) => fn.mock.invocationCallOrder[at]
    expect(order(fake.source.fetchTurns, turnCall)).toBeLessThan(order(fake.source.listActivities, activityCall))
    expect(order(fake.source.listActivities, activityCall)).toBeLessThan(order(fake.source.listRuns, historyCall))
  })

  it('V10: the final activity read is tried three times, keeps what is shown, and a later catch-up shows the card', async () => {
    const shown = command('k0', after(1, 'run_start'), 'succeeded')
    const { session, source, data } = await open({ activities: { [A]: [shown] } })
    source.startChatRun.mockResolvedValue('r1')
    data.turns[A].push(turn(2, 'ai', 'answer', null, { sourceRunId: 'r1' }))
    data.activities[A] = [shown, change('r1', after(2))]
    await session.invokeAi()
    const before = source.listActivities.mock.calls.length
    source.listActivities
      .mockRejectedValueOnce(httpError(503, 'isolated read 1'))
      .mockRejectedValueOnce(httpError(503, 'isolated read 2'))
      .mockRejectedValueOnce(httpError(503, 'isolated read 3'))
    await poll() // finished: the lock goes first, then the first read fails
    expect(session.state.runId).toBeNull()
    expect(source.listActivities.mock.calls.length).toBe(before + 1)
    await poll(FINAL_ACTIVITY_READ_ATTEMPTS - 1)
    expect(source.listActivities.mock.calls.length).toBe(before + FINAL_ACTIVITY_READ_ATTEMPTS)
    await poll(3)
    expect(source.listActivities.mock.calls.length).toBe(before + FINAL_ACTIVITY_READ_ATTEMPTS)
    expect(session.state.activities.filter((a) => a.kind !== 'status').map((a) => a.id)).toEqual(['k0'])
    expect(codes(session)).toEqual([])
    expect(session.state.notice).toBeNull()

    await session.selectChat(A)
    expect(placed(session)).toEqual(['turn:1', 'command:k0', 'turn:2', 'change:r1'])
    expect(codes(session)).toEqual([])
  })

  it('V10b: an older activity read that answers after a newer one is dropped', async () => {
    const { session, source, data } = await open({ activities: { [A]: [command('k1', after(1, 'run_start'))] } })
    let release!: (list: ConversationActivity[]) => void
    source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => { release = resolve }))
    const slow = session.catchUp()
    await flushPromises()
    expect(release).toBeTypeOf('function')
    data.activities[A] = [command('k1', after(1, 'run_start'), 'succeeded')]
    await session.catchUp()
    expect(commandState(session, 'k1')).toBe('succeeded')
    release([command('k1', after(1, 'run_start'))])
    await slow
    expect(commandState(session, 'k1')).toBe('succeeded')
  })

  it('V10c: a catch-up read started before a decision answered is dropped; the decided card is not set back', async () => {
    const pendingK1 = command('k1', after(1, 'run_start'))
    const { session, source } = await open({ activities: { [A]: [pendingK1] } })
    let releaseStale!: () => void
    source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => { releaseStale = () => resolve([pendingK1]) }))
    const catching = session.catchUp()
    await flushPromises()
    expect(releaseStale).toBeTypeOf('function')
    let releaseFresh!: () => void
    source.listActivities.mockImplementationOnce(
      () => new Promise<ConversationActivity[]>((resolve) => { releaseFresh = () => resolve([{ ...pendingK1, state: 'running' }]) }))
    const deciding = session.decideCommand('k1', 'approve')
    expect(commandState(session, 'k1')).toBe('approving') // the held, optimistic state
    await flushPromises()
    expect(commandState(session, 'k1')).toBe('approved')
    releaseStale()
    await flushPromises()
    expect(commandState(session, 'k1')).toBe('approved')
    releaseFresh()
    await deciding
    await catching
    expect(commandState(session, 'k1')).toBe('running')
  })

  it('V8b: answers for chat A that land after B opened change nothing in B and lock nothing', async () => {
    const cmdA = command('kA', after(1, 'run_start'))
    const cmdB = command('kB', after(1, 'run_start'))
    const fake = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'a')], [B]: [turn(1, 'user', 'b'), turn(2, 'ai', 'b answer')] },
      activities: { [A]: [cmdA], [B]: [cmdB] },
      settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' },
    })
    fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...FINISHED, runId: id }))
    let releaseActivities!: () => void
    fake.source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => {
      releaseActivities = () => resolve([cmdA, change('rA', after(1, 'run_start'))])
    }))
    const session = createConversationSession({ source: fake.source })
    sessions.push(session)
    await session.setScope(SCOPE)
    await flushPromises()
    let releaseTurns!: () => void
    fake.source.fetchTurns.mockImplementationOnce(() => new Promise<TurnsPage>((resolve) => {
      releaseTurns = () => resolve(page([turn(1, 'user', 'a'), turn(5, 'ai', 'late A', null, { sourceRunId: 'rA' })]))
    }))
    const catching = session.catchUp()
    await flushPromises()
    let releasePost!: () => void
    fake.source.postTurn.mockImplementationOnce(() => new Promise((resolve) => {
      releasePost = () => resolve({ turn: turn(6, 'user', 'A question'), headSeq: 6 })
    }))
    const sending = session.send('A question')
    await flushPromises()

    await session.selectChat(B)
    await flushPromises()
    const snapshot = () => ({
      turns: session.state.turns.map((t) => t.seq), activities: session.state.activities.map((a) => a.id),
      head: session.state.headSeq, runId: session.state.runId, pending: session.state.pending.length,
    })
    const before = snapshot()
    expect(before).toEqual({ turns: [1, 2], activities: ['kB'], head: 2, runId: null, pending: 0 })
    releaseActivities()
    releaseTurns()
    releasePost()
    await catching
    await sending
    await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(snapshot()).toEqual(before)
    // flowgate.v02gui.0009: the AI run of A's message still starts — for A, off screen; B shows nothing of it.
    expect(fake.source.startChatRun).toHaveBeenCalledTimes(1)
    expect(fake.source.startChatRun).toHaveBeenCalledWith(A, expect.any(String), expect.any(String))
    expect(session.running.value).toBe(false)
  })

  it('V8c: after A -> B -> A only what the new A generation read is shown', async () => {
    const fake = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'start')], [B]: [turn(1, 'user', 'other chat')] },
      activities: { [A]: [command('kA', after(1, 'run_start'), 'succeeded')] },
    })
    fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...FINISHED, runId: id }))
    let releaseOldActivities!: () => void
    fake.source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => {
      releaseOldActivities = () => resolve([command('kA', after(1, 'run_start'))])
    }))
    const session = createConversationSession({ source: fake.source })
    sessions.push(session)
    await session.setScope(SCOPE)
    await flushPromises()
    let releaseOldTurns!: () => void
    fake.source.fetchTurns.mockImplementationOnce(() => new Promise<TurnsPage>((resolve) => {
      releaseOldTurns = () => resolve(page([turn(1, 'user', 'start'), turn(9, 'ai', 'old generation reply')]))
    }))
    const oldCatchUp = session.catchUp()
    await flushPromises()

    await session.selectChat(B)
    await flushPromises()
    await session.selectChat(A)
    await flushPromises()
    expect(commandState(session, 'kA')).toBe('succeeded')
    releaseOldActivities()
    releaseOldTurns()
    await oldCatchUp
    await flushPromises()
    expect(session.state.activeChatId).toBe(A)
    expect(commandState(session, 'kA')).toBe('succeeded')
    expect(session.state.turns.map((t) => t.seq)).toEqual([1])
  })

  it.each([true, false])('V8d: a POST that ends after a switch catches A up only when A is shown again (back on A=%s)', async (back) => {
    const { session, source, data } = await open()
    let releasePost!: () => void
    source.postTurn.mockImplementationOnce((_chatId: string, input: { body: string }) => new Promise((resolve) => {
      releasePost = () => {
        const saved = turn(2, 'user', input.body)
        data.turns[A].push(saved)
        resolve({ turn: saved, headSeq: 2 })
      }
    }))
    const sending = session.send('follow-up')
    await flushPromises()
    await session.selectChat(B)
    await flushPromises()
    if (back) {
      await session.selectChat(A)
      await flushPromises()
    }
    data.activities[A] = [command('k9', after(2, 'run_start'))]
    const reads = readsOf(source.listActivities, A)
    releasePost()
    await sending
    await flushPromises()
    if (back) {
      expect(readsOf(source.listActivities, A)).toBe(reads + 1)
      expect(placed(session)).toEqual(['turn:1', 'turn:2', 'command:k9'])
    } else {
      expect(readsOf(source.listActivities, A)).toBe(reads)
      expect(session.state.activeChatId).toBe(B)
      expect(session.state.activities).toEqual([])
    }
  })

  it('V11: chat A polling tick answers after B opened: no run line, card, lock or settings reach B', async () => {
    const { session, source, data } = await open()
    let detail = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    let releaseTickRead!: () => void
    source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => {
      releaseTickRead = () => resolve([command('kA', after(1, 'run_start')), change('r1', after(1, 'run_start'))])
    }))
    await poll() // running tick, its activity read held
    expect(releaseTickRead).toBeTypeOf('function')
    await session.selectChat(B)
    await flushPromises()
    const runReads = source.fetchRun.mock.calls.length
    const settings = source.fetchChatSettings.mock.calls.length
    detail = { ...FAILED, endReason: 'timeout' }
    data.turns[A].push(turn(2, 'ai', 'late A answer', null, { sourceRunId: 'r1' }))
    releaseTickRead()
    await poll(5)
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.runId).toBeNull()
    expect(session.running.value).toBe(false)
    expect(session.state.activities).toEqual([])
    expect(session.state.turns.map((t) => t.body)).toEqual(['other chat'])
    expect(source.fetchRun).toHaveBeenCalledTimes(runReads)
    expect(source.fetchChatSettings).toHaveBeenCalledTimes(settings)
  })

  it('V11: a final turn read held across the switch renders nothing in B and starts no activity retry for A', async () => {
    const { session, source } = await open()
    serve(source, { r1: () => ({ ...FAILED, endReason: 'timeout' }) })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    let releaseFinal!: () => void
    source.fetchTurns.mockImplementationOnce(() => new Promise<TurnsPage>((resolve) => {
      releaseFinal = () => resolve(page([turn(1, 'user', 'start'), turn(2, 'ai', 'A answer', null, { sourceRunId: 'r1' })]))
    }))
    await poll() // finished: the final turn read is held
    expect(releaseFinal).toBeTypeOf('function')
    await session.selectChat(B)
    await flushPromises()
    const aReads = readsOf(source.listActivities, A)
    const settings = source.fetchChatSettings.mock.calls.length
    releaseFinal()
    await poll(FINAL_ACTIVITY_READ_ATTEMPTS)
    expect(session.state.activeChatId).toBe(B)
    expect(codes(session)).toEqual([])
    expect(session.state.turns.map((t) => t.body)).toEqual(['other chat'])
    expect(readsOf(source.listActivities, A)).toBe(aReads)
    expect(source.fetchChatSettings).toHaveBeenCalledTimes(settings)
  })
})

describe('0151 T-A2 follow-up question: cards stay where the server anchored them (V6a-V6d4, V12)', () => {
  const INVOKE = { settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' as const } }

  function savesAs(source: { postTurn: ReturnType<typeof vi.fn> }, data: { turns: Record<string, ConversationTurn[]> }, seq: number) {
    source.postTurn.mockImplementationOnce(async (_chatId: string, input: { body: string }) => {
      const saved = turn(seq, 'user', input.body)
      data.turns[A].push(saved)
      return { turn: saved, headSeq: seq }
    })
  }

  it('V6a: a run the server finished (no 409) shows its card after #9 on revisit, before #10 starts a new run', async () => {
    const { session, source, data } = await open({ ...INVOKE, turns: { [A]: [turn(8, 'user', 'fix it')] } })
    // Server side: R_old answered #9 and finished after the client stopped tracking it.
    data.turns[A].push(turn(9, 'ai', 'fixed', null, { sourceRunId: 'R_old' }))
    data.activities[A] = [change('R_old', after(9), ['src/old.ts'])]
    await session.catchUp() // what coming back to the panel runs
    expect(placed(session)).toEqual(['turn:8', 'turn:9', 'change:R_old'])

    const activeCalls = source.fetchActiveChatRun.mock.calls.length
    let rNew = RUNNING
    serve(source, { R_new: () => rNew })
    savesAs(source, data, 10)
    source.startChatRun.mockResolvedValueOnce('R_new')
    expect(await session.send('and the tests?')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.state.runId).toBe('R_new')
    await poll()
    expect(placed(session)).toEqual(['turn:8', 'turn:9', 'change:R_old', 'turn:10'])

    data.turns[A].push(turn(11, 'ai', 'tests added', null, { sourceRunId: 'R_new' }))
    data.activities[A] = [change('R_old', after(9), ['src/old.ts']), change('R_new', after(11), ['src/old.test.ts'])]
    rNew = FINISHED
    await poll()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(placed(session)).toEqual(['turn:8', 'turn:9', 'change:R_old', 'turn:10', 'turn:11', 'change:R_new'])
    expect(cards(session).map((c) => [c.runId, c.files.map((f) => f.path)])).toEqual([
      ['R_old', ['src/old.ts']], ['R_new', ['src/old.test.ts']]])
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(activeCalls) // no adoption path
  })

  it('V6a2: #10 while R_old is still active adopts it (409); its late answer #11 carries the card', async () => {
    const { session, source, data } = await open({ ...INVOKE, turns: { [A]: [turn(9, 'user', 'fix it')] } })
    let rOld = RUNNING
    serve(source, { R_old: () => rOld })
    data.activities[A] = [change('R_old', after(9, 'run_start'))]
    savesAs(source, data, 10)
    source.startChatRun.mockRejectedValueOnce(new ConversationRequestError('busy', 409, 'run_in_progress', 'R_old'))
    await session.send('also the tests')
    await flushPromises()
    expect(session.state.runId).toBe('R_old')
    await poll()
    expect(source.fetchRun).toHaveBeenLastCalledWith('R_old')
    expect(session.state.runId).toBe('R_old')
    expect(placed(session)).toEqual(['turn:9', 'change:R_old', 'turn:10'])

    data.turns[A].push(turn(11, 'ai', 'done, tests too', null, { sourceRunId: 'R_old', basedOnSeq: 9, staleSinceSeq: 10 }))
    data.activities[A] = [change('R_old', after(11))]
    rOld = FINISHED
    await poll()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(placed(session)).toEqual(['turn:9', 'turn:10', 'turn:11', 'change:R_old'])
    expect(cards(session)[0].runId).toBe('R_old')
    expect(session.state.turns.find((t) => t.seq === 11)).toMatchObject({ sourceRunId: 'R_old', staleSinceSeq: 10 })
  })

  it('V6a3: a 409 for a run that answered #9 and then reads finished places the card after #9', async () => {
    const { session, source, data } = await open({ ...INVOKE, turns: { [A]: [turn(8, 'user', 'fix it')] } })
    // R_old answered before #10 but still held its lease: the start is refused, the next read is already finished.
    data.turns[A].push(turn(9, 'ai', 'fixed', null, { sourceRunId: 'R_old' }))
    data.activities[A] = [change('R_old', after(9))]
    savesAs(source, data, 10)
    source.startChatRun.mockRejectedValueOnce(new ConversationRequestError('busy', 409, 'run_in_progress', 'R_old'))
    await session.send('and the docs?')
    await flushPromises()
    expect(session.state.runId).toBe('R_old')
    await poll()
    expect(source.fetchRun).toHaveBeenCalledWith('R_old')
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(placed(session)).toEqual(['turn:8', 'turn:9', 'change:R_old', 'turn:10'])
  })

  it('V6b: a late answer #11 of R_old keeps its card after #11, R_new keeps its own', async () => {
    const { session } = await open({
      turns: { [A]: [turn(9, 'user', 'fix it'), turn(10, 'user', 'and the tests'),
        turn(11, 'ai', 'fixed', null, { sourceRunId: 'R_old', basedOnSeq: 9, staleSinceSeq: 10 }),
        turn(12, 'ai', 'tests', null, { sourceRunId: 'R_new' })] },
      activities: { [A]: [change('R_old', after(11)), change('R_new', after(12))] },
    })
    const expected = ['turn:9', 'turn:10', 'turn:11', 'change:R_old', 'turn:12', 'change:R_new']
    expect(placed(session)).toEqual(expected)
    await session.catchUp()
    await session.catchUp()
    expect(placed(session)).toEqual(expected)
    expect(codes(session)).toEqual([])
    for (const card of cards(session)) {
      expect(session.state.turns.find((t) => t.seq === card.anchor?.seq)?.sourceRunId).toBe(card.runId)
    }
  })

  it('V6c: run_start (9) becomes reply (11) on a later read; an older read answering late does not set it back', async () => {
    const { session, source, data } = await open({
      turns: { [A]: [turn(9, 'user', 'fix it'), turn(10, 'user', 'and the tests')] },
      activities: { [A]: [change('R_old', after(9, 'run_start'))] },
    })
    expect(placed(session)).toEqual(['turn:9', 'change:R_old', 'turn:10'])
    let releaseOld!: () => void
    source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => {
      releaseOld = () => resolve([change('R_old', after(9, 'run_start'))])
    }))
    const older = session.catchUp()
    await flushPromises()
    data.turns[A].push(turn(11, 'ai', 'fixed', null, { sourceRunId: 'R_old', basedOnSeq: 9, staleSinceSeq: 10 }))
    data.activities[A] = [change('R_old', after(11))]
    await session.catchUp()
    expect(placed(session)).toEqual(['turn:9', 'turn:10', 'turn:11', 'change:R_old'])
    releaseOld()
    await older
    expect(placed(session)).toEqual(['turn:9', 'turn:10', 'turn:11', 'change:R_old'])
    expect(cards(session)[0].anchor).toEqual(after(11))
  })

  // R_old answered #6 and #11; #12 is someone else's later message, so "last" and "after the last answer" differ.
  const MANY = [turn(5, 'user', 'fix it'), turn(6, 'ai', 'first part', null, { sourceRunId: 'R_old' }), turn(10, 'user', 'and the tests'),
    turn(11, 'ai', 'second part', null, { sourceRunId: 'R_old' }), turn(12, 'user', 'thanks')]

  it('V6d / V6d2: with a start seq the card follows the last answer; without one the server leaves it unplaced', async () => {
    const withStart = await open({ turns: { [A]: MANY.map((t) => ({ ...t })) }, activities: { [A]: [change('R_old', after(11))] } })
    const withoutStart = await open({ turns: { [A]: MANY.map((t) => ({ ...t })) }, activities: { [A]: [change('R_old', unplaced('ambiguous'))] } })
    expect(placed(withStart.session)).toEqual(['turn:5', 'turn:6', 'turn:10', 'turn:11', 'change:R_old', 'turn:12'])
    expect(placed(withoutStart.session)).toEqual(['turn:5', 'turn:6', 'turn:10', 'turn:11', 'turn:12', 'change:R_old'])
    for (const { session } of [withStart, withoutStart]) {
      await session.catchUp()
      expect(codes(session)).toEqual([])
    }
    expect(placed(withStart.session)).toEqual(['turn:5', 'turn:6', 'turn:10', 'turn:11', 'change:R_old', 'turn:12'])
    expect(placed(withoutStart.session)).toEqual(['turn:5', 'turn:6', 'turn:10', 'turn:11', 'turn:12', 'change:R_old'])
    expect(cards(withoutStart.session)[0].anchor).toEqual(unplaced('ambiguous'))
  })

  it('V6d3: without a start seq a single answer is still a reply anchor', async () => {
    const { session } = await open({
      turns: { [A]: [turn(5, 'user', 'fix it'), turn(11, 'ai', 'only answer', null, { sourceRunId: 'R_old' }), turn(12, 'user', 'thanks')] },
      activities: { [A]: [change('R_old', after(11))] },
    })
    expect(placed(session)).toEqual(['turn:5', 'turn:11', 'change:R_old', 'turn:12'])
    expect(codes(session)).toEqual([])
  })

  it('V6d4: no start seq and no answer stays last; start seq 0 is before the first turn, not "none"', async () => {
    const turns = () => [turn(1, 'user', 'fix it'), turn(2, 'user', 'still there?')]
    const none = await open({ turns: { [A]: turns() }, activities: { [A]: [change('R_old', unplaced('unresolved'))] } })
    const zero = await open({ turns: { [A]: turns() }, activities: { [A]: [change('R_old', { seq: 0, position: 'after', state: 'run_start' })] } })
    // R_old has no answer, so the restore may add its own run line; only the card placement is asserted here.
    expect(placed(none.session)).toEqual(['turn:1', 'turn:2', 'change:R_old'])
    expect(placed(zero.session)).toEqual(['change:R_old', 'turn:1', 'turn:2'])
    await none.session.catchUp()
    await zero.session.catchUp()
    expect(placed(none.session)).toEqual(['turn:1', 'turn:2', 'change:R_old'])
    expect(placed(zero.session)).toEqual(['change:R_old', 'turn:1', 'turn:2'])
  })

  it('V12: each run card sits next to its own answer by anchor, not by time; an unanchored card is last', async () => {
    const { session } = await open({
      turns: { [A]: [turn(5, 'user', 'fix a'), turn(6, 'ai', 'a fixed', null, { sourceRunId: 'R1' }),
        turn(7, 'user', 'fix b'), turn(8, 'ai', 'b fixed', null, { sourceRunId: 'R2' })] },
      activities: { [A]: [
        change('R2', after(8), ['src/b.ts'], '2026-10-06T08:00:00Z'), // older by time, later by anchor
        change('R1', after(6), ['src/a.ts'], '2026-10-06T09:00:00Z'),
        command('k3', unplaced(null)),
      ] },
    })
    expect(placed(session)).toEqual(['turn:5', 'turn:6', 'change:R1', 'turn:7', 'turn:8', 'change:R2', 'command:k3'])
    expect(cards(session).map((c) => [c.runId, c.files[0].path])).toEqual([['R2', 'src/b.ts'], ['R1', 'src/a.ts']])
    for (const card of cards(session)) {
      expect(session.state.turns.find((t) => t.seq === card.anchor?.seq)?.sourceRunId).toBe(card.runId)
    }
    expect(codes(session)).toEqual([])
  })
})

describe('0151 T-A2 fresh session, cancellation and retries (V7, V9c, V9d, V9e)', () => {
  it.each([false, true])('V7: a fresh session restores each past run once (active run=%s)', async (active) => {
    const runs: Record<string, ChatRunState> = {
      ok: { ...FINISHED, runId: 'ok' }, bad: { ...FAILED, runId: 'bad' }, stop: { ...CANCELLED, runId: 'stop' },
    }
    const fake = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'q1'), turn(2, 'ai', 'a1', null, { sourceRunId: 'ok' }), turn(3, 'user', 'q2'),
        turn(4, 'ai', 'partial', null, { sourceRunId: 'bad' }), turn(5, 'user', 'q3'), turn(6, 'ai', 'stopped', null, { sourceRunId: 'stop' })] },
      activities: { [A]: [change('ok', after(2))] },
      runDetails: runs,
      activeRun: active ? 'live' : null,
    })
    fake.source.fetchRun.mockImplementation(async (id: string) => id === 'live' ? { ...RUNNING, runId: 'live' } : { ...runs[id] })
    for (let reload = 0; reload < 2; reload++) {
      const session = createConversationSession({ source: fake.source })
      sessions.push(session)
      const reads = fake.source.listActivities.mock.calls.length
      await session.setScope(SCOPE)
      await flushPromises()
      expect(fake.source.listActivities.mock.calls.length).toBe(reads + 1)
      expect(session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([
        { runId: 'bad', tone: 'danger', code: 'run_failed' },
        { runId: 'stop', tone: 'info', code: 'run_cancelled' },
      ])
      expect(new Set(session.state.activities.map((a) => a.id)).size).toBe(session.state.activities.length)
      expect(placed(session)).toEqual(['turn:1', 'turn:2', 'change:ok', 'turn:3', 'turn:4', 'turn:5', 'turn:6'])
      expect(session.state.runId).toBe(active ? 'live' : null)
      expect(session.running.value).toBe(active)
      session.dispose()
    }
    expect(fake.source.startChatRun).not.toHaveBeenCalled()
  })

  it('V9c: a failed cancel keeps the lock with one notice; a retried cancel ends as cancelled', async () => {
    const { session, source } = await open()
    let detail = RUNNING
    serve(source, { r1: () => detail })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    source.cancelRun.mockRejectedValueOnce(httpError(500, 'isolated cancel down'))
    await session.stopRun()
    expect(session.state.cancelling).toBe(false)
    expect(session.state.notice).toMatchObject({ tone: 'danger', code: 'cancel_failed' })
    expect(session.state.runId).toBe('r1')
    await poll()
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])

    await session.stopRun()
    expect(source.cancelRun).toHaveBeenCalledTimes(2)
    expect(session.state.cancelling).toBe(true)
    await poll() // still running (cancelling maps to running since T-A1)
    expect(session.state.runId).toBe('r1')
    expect(session.state.cancelling).toBe(true)
    detail = CANCELLED
    await poll()
    expect(session.state.runId).toBeNull()
    expect(session.state.cancelling).toBe(false)
    expect(session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([{ runId: 'r1', tone: 'info', code: 'run_cancelled' }])
  })

  it.each([404, 410])('V9c: a cancel answered %s returns quietly', async (status) => {
    const { session, source } = await open()
    serve(source, { r1: () => RUNNING })
    source.startChatRun.mockResolvedValue('r1')
    await session.invokeAi()
    source.cancelRun.mockRejectedValueOnce(httpError(status, 'isolated gone'))
    await session.stopRun()
    expect(session.state.notice).toBeNull()
    expect(session.state.cancelling).toBe(true)
    expect(session.state.runId).toBe('r1')
  })

  it('V9d: a cancelled R1 keeps its line and card; R2 adds only its own', async () => {
    const { session, source, data } = await open()
    let r1 = RUNNING
    let r2 = RUNNING
    serve(source, { R1: () => r1, R2: () => r2 })
    source.startChatRun.mockResolvedValueOnce('R1').mockResolvedValueOnce('R2')
    await session.invokeAi()
    await session.stopRun()
    data.activities[A] = [change('R1', after(1, 'run_start'), ['src/half.ts'])]
    r1 = CANCELLED
    await poll()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual(['run_cancelled'])

    await session.invokeAi()
    expect(session.state.runId).toBe('R2')
    await poll()
    data.turns[A].push(turn(3, 'ai', 'second try', null, { sourceRunId: 'R2' }))
    data.activities[A] = [change('R1', after(1, 'run_start'), ['src/half.ts']), change('R2', after(3), ['src/full.ts'])]
    r2 = FINISHED
    await poll()
    expect(session.state.runId).toBeNull()
    expect(session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([{ runId: 'R1', code: 'run_cancelled' }])
    expect(placed(session)).toEqual(['turn:1', 'change:R1', 'turn:3', 'change:R2'])
  })

  it('V9e: a failed send keeps its key; the same text again reuses it and runs the send action once', async () => {
    const { session, source } = await open({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' } })
    source.postTurn.mockRejectedValueOnce(httpError(422, 'isolated refused'))
    expect(await session.send('hello')).toBe(false)
    expect(session.state.pending).toMatchObject([{ body: 'hello', failed: true }])
    expect(source.startChatRun).not.toHaveBeenCalled()
    source.startChatRun.mockResolvedValue('r1')
    expect(await session.send('hello')).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(2)
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).toBe(source.postTurn.mock.calls[0][1].idempotencyKey)
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.state.pending).toEqual([])
  })
})
