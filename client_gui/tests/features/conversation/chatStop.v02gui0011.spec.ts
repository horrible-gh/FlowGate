import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { createConversationSession } from '@/features/conversation/conversationSession'
import type { AiRunState } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02gui.0011 B — stop right after the send. Measured on stage: from the press of send the stop button stayed
// off while the chat was created (7.8s), the message stored and /ai-invoke/start answered (17-50s): no run id existed
// yet and the server cancelled by run id only. Now the stop works in each of those phases:
// - message being stored (or its chat created): the message is kept, the AI call is not started;
// - AI call starting: the press is stopped by its request id (POST /ai-invoke/start/cancel);
// - a stop without an answer is shown as unconfirmed and kept in sessionStorage, so an F5 still delivers it.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const STOPS_KEY = 'flowgate.conversation.startStops'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function setup(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'stop', updatedAt: '2026-11-14T07:00:00Z' }],
    turns: { [A]: [turn(1, 'user', 'first', '2026-11-14T07:00:00Z')] },
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  return { ...fake, session }
}

const noAnswer = () => new ConversationRequestError('timeout of 30000ms exceeded', null, null)
const dangerLines = (session: ReturnType<typeof setup>['session']) =>
  session.state.activities.filter((a) => a.kind === 'status' && (a as { tone?: string }).tone === 'danger')

beforeEach(() => sessionStorage.clear())

describe('v02gui.0011 B: stop right after the send', () => {
  it('a stop while the message is being stored keeps the message and starts no AI call', async () => {
    const { session, source } = setup({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' } })
    await session.setScope(SCOPE)
    await flushPromises()
    const post = deferred<{ turn: ReturnType<typeof turn>; headSeq: number }>()
    source.postTurn.mockImplementationOnce(() => post.promise)
    const sending = session.send('oops')
    await flushPromises()
    expect(session.state.sending).toBe(true)
    expect(session.startStop.value).toBe('available')
    await session.stopRun()
    expect(session.startStop.value).toBe('requested')
    post.resolve({ turn: turn(2, 'user', 'oops', '2026-11-14T07:01:00Z'), headSeq: 2 })
    expect(await sending).toBe(true)
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(session.state.turns.map((t) => t.body)).toContain('oops')
    expect(session.state.notice?.code).toBe('start_stopped')
    expect(session.startStop.value).toBe('none')
  })

  it('a send that was not stopped still calls the AI', async () => {
    const { session, source } = setup({ settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' } })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('a stop while the AI call is starting stops the press by its request id', async () => {
    const { session, source } = setup()
    source.cancelChatStart = vi.fn(async () => ({ state: 'cancelled', runId: null }))
    await session.setScope(SCOPE)
    await flushPromises()
    const start = deferred<string | null>()
    source.startChatRun.mockImplementationOnce(() => start.promise)
    const pressing = session.invokeAi()
    await flushPromises()
    expect(session.state.runId).toBe('starting')
    expect(session.startStop.value).toBe('available')
    await session.stopRun()
    const requestId = source.startChatRun.mock.calls[0][2]
    expect(source.cancelChatStart).toHaveBeenCalledWith(A, requestId)
    expect(session.state.runId).toBeNull()
    expect(session.state.cancelling).toBe(false)
    expect(session.state.notice?.code).toBe('start_stopped')
    // The /start still on its way is refused by the server; nothing more is shown.
    start.reject(httpError(409, 'stopped', 'start_cancelled'))
    await pressing
    await flushPromises()
    expect(session.state.runId).toBeNull()
    expect(dangerLines(session)).toEqual([])
    expect(sessionStorage.getItem(STOPS_KEY)).toBeNull()
  })

  it('a stop while the run is being admitted follows the run the server stops', async () => {
    const run: AiRunState = { runId: 'run-9', status: 'finished', endReason: 'cancelled', outcome: 'none', exitCode: null,
      docRef: A, actionScope: 'chat', groupId: null } as AiRunState
    const { session, source } = setup({ runDetails: { 'run-9': run } })
    source.cancelChatStart = vi.fn(async () => ({ state: 'cancel_pending', runId: null }))
    await session.setScope(SCOPE)
    await flushPromises()
    const start = deferred<string | null>()
    source.startChatRun.mockImplementationOnce(() => start.promise)
    const pressing = session.invokeAi()
    await flushPromises()
    await session.stopRun()
    expect(session.state.runId).toBe('starting')
    expect(session.state.cancelling).toBe(true)
    expect(session.startStop.value).toBe('requested')
    start.resolve('run-9')
    await pressing
    await flushPromises()
    expect(source.fetchRun).toHaveBeenCalledWith('run-9')
    expect(session.state.runId).toBeNull()
    expect(session.state.cancelling).toBe(false)
    expect(dangerLines(session)).toEqual([])
  })

  it('a stop with no answer is shown as unconfirmed and delivered again after an F5', async () => {
    const { session, source } = setup()
    source.cancelChatStart = vi.fn(async () => { throw noAnswer() })
    await session.setScope(SCOPE)
    await flushPromises()
    const start = deferred<string | null>()
    source.startChatRun.mockImplementationOnce(() => start.promise)
    const pressing = session.invokeAi()
    await flushPromises()
    await session.stopRun()
    const requestId = source.startChatRun.mock.calls[0][2]
    expect(session.state.notice?.code).toBe('cancel_unconfirmed')
    expect(session.state.cancelling).toBe(false)
    expect(session.state.runId).toBe('starting') // the button can be pressed again
    expect(session.startStop.value).toBe('available')
    expect(JSON.parse(sessionStorage.getItem(STOPS_KEY) ?? '[]')).toMatchObject([{ chatId: A, requestId }])

    // F5: a new page (session) sends the kept stop once and forgets it on the answer.
    const reloaded = createFakeSource()
    reloaded.source.cancelChatStart = vi.fn(async () => ({ state: 'cancelling', runId: 'run-1' }))
    createConversationSession({ source: reloaded.source, sleep: async () => undefined })
    await flushPromises()
    expect(reloaded.source.cancelChatStart).toHaveBeenCalledWith(A, requestId)
    expect(sessionStorage.getItem(STOPS_KEY)).toBeNull()

    start.resolve(null)
    await pressing
  })

  it('a refused stop says so and lets the call go on', async () => {
    const { session, source } = setup()
    source.cancelChatStart = vi.fn(async () => { throw httpError(500, 'boom') })
    await session.setScope(SCOPE)
    await flushPromises()
    const start = deferred<string | null>()
    source.startChatRun.mockImplementationOnce(() => start.promise)
    const pressing = session.invokeAi()
    await flushPromises()
    await session.stopRun()
    expect(session.state.notice?.code).toBe('cancel_failed')
    expect(session.state.cancelling).toBe(false)
    expect(sessionStorage.getItem(STOPS_KEY)).toBeNull()
    start.resolve(null)
    await pressing
  })

  it('without the stop port a starting call cannot be stopped (as before)', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    const start = deferred<string | null>()
    source.startChatRun.mockImplementationOnce(() => start.promise)
    const pressing = session.invokeAi()
    await flushPromises()
    await session.stopRun()
    expect(session.state.runId).toBe('starting')
    expect(session.state.cancelling).toBe(false)
    expect(source.cancelRun).not.toHaveBeenCalled()
    start.resolve(null)
    await pressing
  })
})
