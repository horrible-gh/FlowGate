import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import { readFollowUps } from '@/features/conversation/followUps'
import type { AiRunState } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0067 card 3 (0001-B §3): [지금 멈추고 다시 답하기] on the follow-up line. It stops the running answer
// the way [중지] does and — once the stop is confirmed (the run has ended) — calls the AI once more, which reads the
// waiting messages too. Stop and new call are ONE act: a plain [중지] still never calls again (card 1), two presses
// never make two runs, and no new run starts before the stop is confirmed.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'

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
    chats: [{ id: A, title: 'Chat', updatedAt: '2026-11-17T06:00:00Z' }],
    turns: { [A]: [turn(1, 'user', 'first question'), turn(2, 'ai', 'earlier answer', null, { basedOnSeq: 1 })] },
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

const settle = async () => {
  for (let i = 0; i < 6; i++) await flushPromises()
}
const run = (runId: string, status: 'running' | 'finished', extra: Partial<AiRunState> = {}): AiRunState => ({
  runId, status, endReason: status === 'finished' ? 'cancelled' : null, outcome: null, exitCode: null,
  docRef: A, actionScope: 'chat', groupId: SCOPE.groupId, ...(status === 'finished' ? { finishedAt: new Date(Date.now() + 1000).toISOString() } : {}),
  ...extra,
})

/** The chat is open with one run going on and one message of the person waiting behind it. */
async function waitingChat(extra: Parameters<typeof createFakeSource>[0] = {}) {
  const s = setup(extra)
  await s.session.setScope(SCOPE)
  await flushPromises()
  expect(s.session.state.runId).toBe('run-live')
  expect(await s.session.send('no, I meant the admin login')).toBe(true)
  await settle()
  expect(s.session.followUp.value).toMatchObject({ waitingSeqs: [101], count: 1, stopping: false })
  return s
}

beforeEach(() => {
  localStorage.clear()
})
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
})

describe('0067 card 3: [지금 멈추고 다시 답하기]', () => {
  it('stops the run like [중지] and, once the run has ended, answers once with the new message', async () => {
    const { session, source, data, open } = await waitingChat()

    await session.replyNow()
    expect(source.cancelRun).toHaveBeenCalledTimes(1)
    expect(source.cancelRun).toHaveBeenCalledWith('run-live')
    expect(session.state.cancelling).toBe(true)

    data.runDetails['run-live'] = run('run-live', 'finished') // the stop took effect: cancelled
    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][0]).toBe(A)
    // the stopped run is recorded as a stopped run (what [중지] leaves), the follow-up marks are spent.
    expect(session.state.activities.some((a) => a.kind === 'status' && a.code === 'run_cancelled')).toBe(true)
    expect(session.followUp.value).toBeNull()
    expect(readFollowUps('guest', A).every((m) => m.auto && !m.replyNow)).toBe(true)
  })

  it('does not start the new run before the stop is confirmed (the run is still running)', async () => {
    const { session, source, data, open } = await waitingChat()

    await session.replyNow()
    await settle()
    // cancelRun was sent but the run has not ended: nothing else starts, the line shows the stop under way.
    expect(source.cancelRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(session.state.runId).toBe('run-live')
    expect(session.followUp.value).toMatchObject({ stopping: true })

    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('pressing twice sends one stop and starts one run', async () => {
    const { session, source, data, open } = await waitingChat()

    await Promise.all([session.replyNow(), session.replyNow()])
    await session.replyNow()
    expect(source.cancelRun).toHaveBeenCalledTimes(1)

    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    await session.replyNow() // after it all: nothing waits any more
    await settle()
    expect(source.cancelRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('with several waiting messages: one stop, one new run for all of them', async () => {
    const { session, source, data, open } = await waitingChat()
    expect(await session.send('and the sign-up page too')).toBe(true)
    await settle()
    expect(session.followUp.value).toMatchObject({ waitingSeqs: [101, 102], count: 2 })

    await session.replyNow()
    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    expect(source.cancelRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('is not there without a waiting message: no followUp, and replyNow stops nothing', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.runId).toBe('run-live')
    expect(session.followUp.value).toBeNull()
    await session.replyNow()
    expect(source.cancelRun).not.toHaveBeenCalled()
  })

  it('a message sent with [저장만] does not make the button appear', async () => {
    const { session, source } = setup({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'none' } })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('just a note')).toBe(true)
    await settle()
    expect(session.followUp.value).toBeNull()
    await session.replyNow()
    expect(source.cancelRun).not.toHaveBeenCalled()
  })

  it('a refused stop leaves the run and the waiting message as they were: no new run, the button can be pressed again', async () => {
    const { session, source, data, open } = await waitingChat()
    source.cancelRun.mockRejectedValueOnce(new ConversationRequestError('refused', 500, 'cancel_failed'))

    await session.replyNow()
    await settle()
    expect(session.state.runId).toBe('run-live')
    expect(session.state.cancelling).toBe(false)
    expect(session.followUp.value).toMatchObject({ stopping: false, count: 1 })
    expect(readFollowUps('guest', A).some((m) => m.replyNow)).toBe(false)
    expect(source.startChatRun).not.toHaveBeenCalled()

    // pressed again, this time the stop is accepted.
    await session.replyNow()
    expect(source.cancelRun).toHaveBeenCalledTimes(2)
    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('a failed run is not answered again, even though the stop was pressed', async () => {
    const { session, source, data, open } = await waitingChat()
    await session.replyNow()
    data.runDetails['run-live'] = run('run-live', 'finished', { endReason: 'worker_error', succeeded: false } as Partial<AiRunState>)
    open()
    await settle()
    expect(session.state.runId).toBeNull()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toEqual([])
  })

  it('a plain [중지] after an unanswered stop request cancels the new call (it is the person giving up)', async () => {
    const { session, source, data, open } = await waitingChat()
    // the stop got no answer (network): it may have arrived, so the press stays — until the person presses [중지].
    source.cancelRun.mockRejectedValueOnce(new ConversationRequestError('no answer', null, null))
    await session.replyNow()
    await settle()
    expect(session.state.cancelling).toBe(false)
    expect(readFollowUps('guest', A).some((m) => m.replyNow)).toBe(true)
    expect(session.followUp.value).toMatchObject({ stopping: true })

    await session.stopRun()
    expect(readFollowUps('guest', A).some((m) => m.replyNow)).toBe(false)
    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('[중지] alone still never calls again (card 1 stays as it was)', async () => {
    const { session, source, data, open } = await waitingChat()
    await session.stopRun()
    data.runDetails['run-live'] = run('run-live', 'finished')
    open()
    await settle()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toEqual([])
  })

  it('F5 between the press and the confirmed stop: the new screen still answers once', async () => {
    const first = await waitingChat()
    await first.session.replyNow()
    first.session.dispose() // F5
    first.data.activeRun = null
    first.data.runDetails['run-live'] = run('run-live', 'finished')

    const session = track(createConversationSession({ source: first.source, sleep: async () => undefined }))
    await session.setScope(SCOPE)
    await settle()
    expect(first.source.startChatRun).toHaveBeenCalledTimes(1)
    expect(first.source.startChatRun.mock.calls[0][0]).toBe(A)
  })

  it('edit mode: the line knows the stop may leave an edit half done (the run\'s own access wins once it is read)', async () => {
    const { session } = await waitingChat({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai', sourceAccessMode: 'edit' } })
    // an adopted run (F5): its detail is not read yet, the chat's mode stands in.
    expect(session.followUp.value).toMatchObject({ editsFiles: true })
  })

  it('read mode: no edit warning', async () => {
    const { session } = await waitingChat({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai', sourceAccessMode: 'read_only' } })
    expect(session.followUp.value).toMatchObject({ editsFiles: false })
  })

  it('a run started here in edit mode (the press pins the access) warns', async () => {
    const { session } = setup({
      activeRun: null,
      settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai', sourceAccessMode: 'edit' },
      runs: { 'run-1': ['running', 'running', 'finished'] },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    await session.invokeAi()
    await settle()
    expect(session.state.runId).toBe('run-1')
    expect(await session.send('no, the other one')).toBe(true)
    await settle()
    expect(session.followUp.value).toMatchObject({ count: 1, editsFiles: true })
  })
})
