import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatRunState } from '@/features/conversation/api'
import {
  ACTIVE_RUN_ADOPT_ATTEMPTS,
  RUN_POLL_MS,
  createConversationSession,
  mergeChatRefresh,
  type ConversationSession,
} from '@/features/conversation/conversationSession'
import type { ConversationActivity, TurnsPage, WorkChangeActivity } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0013 — the chat stayed still while its AI run worked: a run missed when the chat opened was never
// polled, and nothing but that poll brought new turns / activities in. session.recover() is the one way back to the
// server's state after a signal (server event, reconnect, return to the panel). These specs pin: a missed run is
// adopted (and the open-time lookup is retried), a polled run is woken instead of read beside, signals during a
// recovery become one more round, a message being sent is never shown twice, and a chat left behind takes nothing.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'

const FINISHED: ChatRunState = { runId: 'r1', docRef: A, actionScope: 'chat', groupId: SCOPE.groupId,
  status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0, succeeded: true }
const RUNNING: ChatRunState = { ...FINISHED, status: 'running', endReason: null, outcome: null, exitCode: null, succeeded: null }

const sessions: ConversationSession[] = []
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
})
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
  vi.useRealTimers()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function change(runId: string, seq: number): WorkChangeActivity {
  return { kind: 'change', id: runId, runId, createdAt: null, anchor: { seq, position: 'after', state: 'reply' },
    filesChanged: 1, insertions: 1, deletions: 0, files: [{ path: 'src/a.ts', status: 'M', insertions: 1, deletions: 0 }] }
}

async function open(init: Parameters<typeof createFakeSource>[0] = {}, before?: (fake: ReturnType<typeof createFakeSource>) => void) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'start')], [B]: [turn(1, 'user', 'other chat')] },
    ...init,
  })
  fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...FINISHED, runId: id }))
  before?.(fake)
  const session = createConversationSession({ source: fake.source })
  sessions.push(session)
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session }
}

const turnsAfter = (fn: { mock: { calls: unknown[][] } }) =>
  fn.mock.calls.filter((call) => (call[1] as { afterSeq?: number }).afterSeq != null).length
const messages = (s: ConversationSession) =>
  s.timeline.value.filter((item) => item.kind === 'message').map((item) => (item.kind === 'message' ? item.turn.body : ''))

describe('v02test.0013 a run the chat missed is adopted', () => {
  it('the active-run lookup of an opening chat is asked again when it fails, and the run is then polled', async () => {
    const { session, source } = await open({ activeRun: 'r1' }, (fake) => {
      fake.source.fetchActiveChatRun.mockRejectedValueOnce(new Error('active query unavailable'))
      fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...RUNNING, runId: id }))
    })
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(1)
    expect(session.state.runId).toBeNull()

    await vi.advanceTimersByTimeAsync(RUN_POLL_MS)
    await flushPromises()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(2)
    expect(session.state.runId).toBe('r1')
    expect(session.running.value).toBe(true)
  })

  it('stops after ACTIVE_RUN_ADOPT_ATTEMPTS failed lookups; the next signal asks again', async () => {
    const { session, source, data } = await open({}, (fake) => {
      fake.source.fetchActiveChatRun.mockRejectedValue(new Error('active query unavailable'))
    })
    await vi.advanceTimersByTimeAsync(RUN_POLL_MS * (ACTIVE_RUN_ADOPT_ATTEMPTS + 2))
    await flushPromises()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(ACTIVE_RUN_ADOPT_ATTEMPTS)

    source.fetchActiveChatRun.mockImplementation(async () => data.activeRun)
    data.activeRun = 'r1'
    source.fetchRun.mockImplementation(async (id: string) => ({ ...RUNNING, runId: id }))
    await session.recover({ run: true })
    await flushPromises()
    expect(session.state.runId).toBe('r1')
  })

  it('a run started elsewhere is adopted by a signal and its first tick reads at once, not after RUN_POLL_MS', async () => {
    const { session, source, data } = await open()
    expect(session.running.value).toBe(false)
    data.activeRun = 'r1'
    source.fetchRun.mockImplementation(async (id: string) => ({ ...RUNNING, runId: id }))
    const turnReads = source.fetchTurns.mock.calls.length

    await session.recover({ run: true })
    await flushPromises()
    expect(session.state.runId).toBe('r1')
    expect(source.fetchRun).toHaveBeenCalledWith('r1')
    expect(source.fetchTurns.mock.calls.length).toBeGreaterThan(turnReads)
  })
})

describe('v02test.0013 a polled run is woken, never read beside', () => {
  it('a signal cuts the poll wait short; the next tick waits RUN_POLL_MS again and no second poll starts', async () => {
    const { session, source, data } = await open()
    source.fetchRun.mockImplementation(async (id: string) => ({ ...RUNNING, runId: id }))
    source.startChatRun.mockResolvedValueOnce('r1')
    await session.invokeAi()
    await flushPromises()
    expect(session.state.runId).toBe('r1')
    expect(source.fetchRun).toHaveBeenCalledTimes(0) // still in its first wait

    data.turns[A].push(turn(2, 'ai', 'progress', null, { sourceRunId: 'r1' }))
    data.activities[A] = [change('r1', 2)]
    const listRuns = source.listRuns.mock.calls.length
    await session.recover({ turns: true, activities: true, history: true })
    await flushPromises()
    expect(source.fetchRun).toHaveBeenCalledTimes(1)
    expect(messages(session)).toEqual(['start', 'progress'])
    expect(session.state.activities.some((a: ConversationActivity) => a.id === 'r1')).toBe(true)
    expect(source.listRuns.mock.calls.length).toBe(listRuns) // no history read while the run is polled

    // One poll: an adoption asking for the same run does not start another.
    data.activeRun = 'r1'
    await session.recover({ run: true })
    await flushPromises()
    const ticks = source.fetchRun.mock.calls.length
    await vi.advanceTimersByTimeAsync(RUN_POLL_MS)
    await flushPromises()
    expect(source.fetchRun.mock.calls.length).toBe(ticks + 1)
  })

  it('a finish signal brings the reply in without waiting for the poll interval', async () => {
    const { session, source, data } = await open()
    let detail: ChatRunState = RUNNING
    source.fetchRun.mockImplementation(async (id: string) => ({ ...detail, runId: id }))
    source.startChatRun.mockResolvedValueOnce('r1')
    await session.invokeAi()
    await vi.advanceTimersByTimeAsync(RUN_POLL_MS)
    await flushPromises()
    expect(session.state.runId).toBe('r1')

    detail = FINISHED
    data.turns[A].push(turn(2, 'ai', 'done', null, { sourceRunId: 'r1' }))
    await session.recover({ turns: true, activities: true, history: true })
    await flushPromises()
    expect(session.state.runId).toBeNull()
    expect(messages(session)).toEqual(['start', 'done'])
  })
})

describe('v02test.0013 one recovery at a time', () => {
  it('signals during a recovery are one more round after it, not reads side by side', async () => {
    const { session, source, data } = await open()
    const first = deferred<ConversationActivity[]>()
    source.listActivities.mockImplementationOnce(() => first.promise)
    const reads = source.listActivities.mock.calls.length
    const turnReads = turnsAfter(source.fetchTurns)

    const running = session.recover({ activities: true })
    await flushPromises()
    void session.recover({ activities: true })
    void session.recover({ turns: true })
    void session.recover({ activities: true })
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(reads + 1)
    expect(turnsAfter(source.fetchTurns)).toBe(turnReads)

    data.turns[A].push(turn(2, 'user', 'from another window'))
    first.resolve([])
    await running
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(reads + 2)
    expect(turnsAfter(source.fetchTurns)).toBe(turnReads + 1)
    expect(messages(session)).toEqual(['start', 'from another window'])
  })

  it('the same turn heard twice is shown once (turns merge by seq)', async () => {
    const { session, data } = await open()
    data.turns[A].push(turn(2, 'ai', 'once'))
    await session.recover({ turns: true })
    await session.recover({ turns: true })
    expect(messages(session)).toEqual(['start', 'once'])
  })

  it('a recovery of a chat left behind changes nothing in the chat now open', async () => {
    const { session, source } = await open()
    const late = deferred<ConversationActivity[]>()
    source.listActivities.mockImplementationOnce(() => late.promise)
    const running = session.recover({ activities: true })
    await flushPromises()
    await session.selectChat(B)
    await flushPromises()
    late.resolve([change('rA', 1)])
    await running
    await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.activities.some((a: ConversationActivity) => a.id === 'rA')).toBe(false)
    expect(messages(session)).toEqual(['other chat'])
  })
})

describe('v02test.0013 held signals', () => {
  it('a signal while a message is being sent waits for it, so the message never shows twice', async () => {
    const { session, source, data } = await open()
    const post = deferred<{ turn: ReturnType<typeof turn>; headSeq: number }>()
    source.postTurn.mockImplementationOnce(() => post.promise)
    const sending = session.send('hello')
    await flushPromises()
    const stored = turn(2, 'user', 'hello')
    data.turns[A].push(stored) // the server stored it; its event arrives before the POST answers
    const turnReads = turnsAfter(source.fetchTurns)

    await session.recover({ turns: true })
    await flushPromises()
    expect(turnsAfter(source.fetchTurns)).toBe(turnReads)
    expect(messages(session)).toEqual(['start', 'hello']) // the sending bubble only

    post.resolve({ turn: { ...stored }, headSeq: 2 })
    await sending
    await flushPromises()
    expect(turnsAfter(source.fetchTurns)).toBe(turnReads + 1) // the held signal, served after the send
    expect(messages(session)).toEqual(['start', 'hello'])
    expect(session.state.pending).toHaveLength(0)
  })

  it('a signal while the chat opens is served once its turns are on screen', async () => {
    const { session, source, data } = await open()
    const first = deferred<TurnsPage>()
    source.fetchTurns.mockImplementationOnce(() => first.promise)
    const opening = session.selectChat(B)
    await flushPromises()
    expect(session.state.turnsStatus).toBe('loading')
    await session.recover({ turns: true })
    data.turns[B].push(turn(2, 'ai', 'arrived while opening'))
    first.resolve({ turns: [turn(1, 'user', 'other chat')], headSeq: 1, nextAfterSeq: null, prevBeforeSeq: null, participants: [], myReadSeq: null })
    await opening
    await flushPromises()
    expect(messages(session)).toEqual(['other chat', 'arrived while opening'])
  })

  it('mergeChatRefresh is the union of two requests', () => {
    expect(mergeChatRefresh(null, { turns: true })).toEqual({ run: false, turns: true, activities: false, history: false })
    expect(mergeChatRefresh({ run: true }, { activities: true })).toEqual({ run: true, turns: false, activities: true, history: false })
  })
})
