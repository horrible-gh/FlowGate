import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import {
  followUpAnswered, readFollowUps, turnReadUpTo, waitingFollowUps, waitingMessageSeqs, withFollowUp, writeFollowUps,
  type FollowUpMark,
} from '@/features/conversation/followUps'
import type { AiRunState } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0067 #46: a message sent with [AI 호출] while the chat's AI answers is answered once that answer
// has ended — judged from the chat (marks kept per user + chat in this browser, AI turns' based_on_seq /
// stale_since_seq), so F5, another chat and another tab do not lose it; never after a stop or a failure, never twice.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

/** A sleep the spec opens: the run's poll waits until `open()`. */
function gate() {
  let opened = false
  const waiters: Array<() => void> = []
  return {
    sleep: (_ms: number) => (opened ? Promise.resolve() : new Promise<void>((resolve) => waiters.push(resolve))),
    open: () => {
      opened = true
      for (const resolve of waiters.splice(0)) resolve()
    },
  }
}

function setup(extra: Parameters<typeof createFakeSource>[0] = {}) {
  const g = gate()
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Chat', updatedAt: '2026-11-17T06:00:00Z' },
      { id: B, title: 'Other', updatedAt: '2026-11-17T05:00:00Z' },
    ],
    turns: { [A]: [turn(1, 'user', 'first question'), turn(2, 'ai', 'earlier answer', null, { basedOnSeq: 1 })], [B]: [] },
    settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' },
    activeRun: 'run-live',
    runs: { 'run-live': ['running', 'finished'] },
    ...extra,
  })
  const session = track(createConversationSession({ source: fake.source, sleep: g.sleep }))
  // like the server: once the run's poll may go on, the run is no longer the chat's active one.
  const open = () => {
    fake.data.activeRun = null
    g.open()
  }
  return { ...fake, session, open }
}

const sessions: ConversationSession[] = []
function track(session: ConversationSession): ConversationSession {
  sessions.push(session)
  return session
}

const waitingSeqs = (s: ConversationSession) =>
  s.timeline.value.filter((item) => item.kind === 'message' && 'waiting' in item && item.waiting).map((item) => item.kind === 'message' ? item.turn.seq : 0)
const followUpLine = (s: ConversationSession) =>
  s.timeline.value.find((item) => item.kind === 'status' && item.activity.kind === 'status' && item.activity.code === 'follow_up_waiting')
const settle = async () => {
  for (let i = 0; i < 6; i++) await flushPromises()
}
const finished = (runId: string, endReason: string, extra: Partial<AiRunState> = {}): AiRunState => ({
  runId, status: 'finished', endReason, outcome: endReason === 'exited' ? 'complete' : 'none', exitCode: endReason === 'exited' ? 0 : null,
  docRef: A, actionScope: 'chat', groupId: SCOPE.groupId, finishedAt: new Date(Date.now() + 1000).toISOString(), ...extra,
})

beforeEach(() => {
  localStorage.clear()
})
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
})

describe('0067 #46: one follow-up after the running answer', () => {
  it('a second message waits (대기 + the follow-up line), then is answered once the first answer ends', async () => {
    const { session, source, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.runId).toBe('run-live')

    expect(await session.send('also the sign-up screen')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(waitingSeqs(session)).toEqual([101])
    expect(session.followUp.value).toMatchObject({ chatId: A, reason: 'run', waitingSeqs: [101], count: 1 })
    expect(followUpLine(session)).toBeTruthy()

    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][0]).toBe(A)
    // the follow-up started: the marks and the line go.
    expect(waitingSeqs(session)).toEqual([])
    expect(followUpLine(session)).toBeUndefined()
    expect(session.followUp.value).toBeNull()
  })

  it('three messages: one follow-up only, on the AI of the latest, and the line counts them', async () => {
    const { session, source, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one')).toBe(true)
    expect(await session.send('two')).toBe(true)
    session.selectProvider('sonnet')
    expect(await session.send('three')).toBe(true)
    await flushPromises()
    expect(waitingSeqs(session)).toEqual([101, 102, 103])
    const line = followUpLine(session)
    expect(line && line.kind === 'status' ? line.activity : null).toMatchObject({ code: 'follow_up_waiting', text: '3' })

    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][1]).toBe('sonnet')
  })

  it('never calls twice for the same message: a follow-up that ends without answering does not call again', async () => {
    const { session, source, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    open()
    await settle()
    await settle()
    // run-live ended -> run-1 (the follow-up) started and ended without a reply: no third run.
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.state.runId).toBeNull()
    expect(readFollowUps('guest', A)).toEqual([])
  })

  it('no follow-up when the answer read the waiting message (based_on_seq)', async () => {
    const { session, source, open } = setup({
      replies: { 'run-live': { chatId: A, turn: turn(102, 'ai', 'answered both', null, { basedOnSeq: 101 }) } },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    open()
    await settle()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toEqual([])
  })

  it('stale_since_seq on the answer: the message it did not see gets the follow-up', async () => {
    const { session, source, open } = setup({
      replies: { 'run-live': { chatId: A, turn: turn(102, 'ai', 'answered the first only', null, { staleSinceSeq: 101 }) } },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('[저장만] (send action none): no 대기, no line, no follow-up', async () => {
    const { session, source, open } = setup({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'none' } })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('just a note')).toBe(true)
    await flushPromises()
    expect(waitingSeqs(session)).toEqual([])
    expect(followUpLine(session)).toBeUndefined()
    open()
    await settle()
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('after [중지] (the run ends cancelled) there is no follow-up; the message stays stored', async () => {
    const { session, source, data, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    await flushPromises()
    await session.stopRun()
    expect(source.cancelRun).toHaveBeenCalledWith('run-live')
    data.runDetails['run-live'] = finished('run-live', 'cancelled')
    open()
    await settle()
    expect(session.state.runId).toBeNull()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(waitingSeqs(session)).toEqual([])
    expect(readFollowUps('guest', A)).toEqual([])
    expect(data.turns[A].some((t) => t.body === 'one more')).toBe(true)
  })

  it('after a failed run there is no follow-up', async () => {
    const { session, source, data, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    await flushPromises()
    data.runDetails['run-live'] = finished('run-live', 'worker_error', { succeeded: false } as Partial<AiRunState>)
    open()
    await settle()
    expect(session.state.runId).toBeNull()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toEqual([])
  })
})

describe('0067 #46: judged from the chat, not the screen', () => {
  it('F5 while the run goes on: the new screen shows 대기 again and answers once the run ends', async () => {
    const first = setup()
    await first.session.setScope(SCOPE)
    await flushPromises()
    expect(await first.session.send('one more')).toBe(true)
    await flushPromises()
    first.session.dispose() // F5

    const g = gate()
    const session = track(createConversationSession({ source: first.source, sleep: g.sleep }))
    await session.setScope(SCOPE)
    await settle()
    expect(session.state.runId).toBe('run-live')
    expect(waitingSeqs(session)).toEqual([101])
    expect(followUpLine(session)).toBeTruthy()
    expect(first.source.startChatRun).not.toHaveBeenCalled()

    first.data.activeRun = null
    g.open()
    await settle()
    expect(first.source.startChatRun).toHaveBeenCalledTimes(1)
    session.dispose()
  })

  it('F5 after the run ended (nobody watched its end): opening the chat answers once', async () => {
    const first = setup()
    await first.session.setScope(SCOPE)
    await flushPromises()
    expect(await first.session.send('one more')).toBe(true)
    await flushPromises()
    first.session.dispose()
    first.data.activeRun = null
    first.data.runs['run-live'] = ['finished']

    const session = track(createConversationSession({ source: first.source, sleep: async () => undefined }))
    await session.setScope(SCOPE)
    await settle()
    expect(first.source.startChatRun).toHaveBeenCalledTimes(1)
    expect(first.source.startChatRun.mock.calls[0][0]).toBe(A)
    session.dispose()
  })

  it('F5 after a stop: the chat\'s last run (cancelled) decides — no follow-up', async () => {
    const first = setup()
    await first.session.setScope(SCOPE)
    await flushPromises()
    expect(await first.session.send('one more')).toBe(true)
    await flushPromises()
    first.session.dispose()
    first.data.activeRun = null
    first.data.runDetails['run-live'] = finished('run-live', 'cancelled')

    const session = track(createConversationSession({ source: first.source, sleep: async () => undefined }))
    await session.setScope(SCOPE)
    await settle()
    expect(first.source.startChatRun).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toEqual([])
    session.dispose()
  })

  it('another chat, then back: the message is still answered once', async () => {
    const { session, source, data, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('one more')).toBe(true)
    await flushPromises()
    await session.selectChat(B)
    open()
    await settle()
    expect(source.startChatRun).not.toHaveBeenCalled() // nothing runs in a chat that is not open
    data.activeRun = null
    data.runs['run-live'] = ['finished']
    await session.selectChat(A)
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][0]).toBe(A)
  })

  it('another tab already runs the chat (409 run_in_progress): that run is followed, then one follow-up', async () => {
    const { session, source, data, open } = setup({ activeRun: null, runs: { 'run-tab': ['running', 'finished'] } })
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(new ConversationRequestError('busy', 409, 'run_in_progress', 'run-tab'))
    expect(await session.send('one more')).toBe(true)
    await settle()
    expect(session.state.runId).toBe('run-tab')
    expect(waitingSeqs(session)).toEqual([101])
    data.activeRun = null
    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.state.runId).not.toBe('run-tab')
  })
})

describe('0067 #46: followUps.ts', () => {
  const mark = (seq: number, extra: Partial<FollowUpMark> = {}): FollowUpMark => ({ seq, providerId: 'opus', at: Date.now(), ...extra })

  it('what an AI turn read: based_on_seq, else before stale_since_seq, else nothing known', () => {
    expect(turnReadUpTo(turn(5, 'ai', 'x', null, { basedOnSeq: 4 }))).toBe(4)
    expect(turnReadUpTo(turn(5, 'ai', 'x', null, { staleSinceSeq: 3 }))).toBe(2)
    expect(turnReadUpTo(turn(5, 'ai', 'x'))).toBe(-1)
    const turns = [turn(3, 'user', 'q'), turn(4, 'user', 'q2'), turn(5, 'ai', 'a', null, { staleSinceSeq: 4 })]
    expect(followUpAnswered(turns, 3)).toBe(true)
    expect(followUpAnswered(turns, 4)).toBe(false)
    expect(waitingFollowUps(turns, [mark(3), mark(4), mark(4.5, { auto: true })]).map((m) => m.seq)).toEqual([4])
    expect(waitingMessageSeqs(turns, [mark(3), mark(4)])).toEqual([4])
  })

  it('kept per user and chat; expired and malformed marks are left out; an auto mark stays auto', () => {
    writeFollowUps('u1', A, [mark(7), mark(3, { at: Date.now() - 25 * 60 * 60 * 1000 })])
    localStorage.setItem('flowgate.user.u1.chat.followups.x', '{"bad":1}')
    expect(readFollowUps('u1', A).map((m) => m.seq)).toEqual([7])
    expect(readFollowUps('u2', A)).toEqual([])
    expect(readFollowUps('u1', 'x')).toEqual([])
    expect(withFollowUp([mark(7, { auto: true })], mark(7, { providerId: 'sonnet' }))).toEqual([expect.objectContaining({ seq: 7, providerId: 'sonnet', auto: true })])
    writeFollowUps('u1', A, [])
    expect(localStorage.getItem('flowgate.user.u1.chat.followups.' + A)).toBeNull()
  })
})
