import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { ConversationRequestError } from '@/features/conversation/api'
import { RUN_ADOPT_GRACE_MS, RUN_POLL_MS, createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0084 T#2 — a slow server must not make a run that exists look failed: a run
// adopted before the server registered it, and a /start that got no answer.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'

/** A sleep the spec releases one polling tick at a time. */
function manualSleep() {
  const waiting: Array<() => void> = []
  const sleep = vi.fn((_ms: number) => new Promise<void>((resolve) => waiting.push(resolve)))
  return {
    sleep,
    async tick() {
      await flushPromises()
      waiting.shift()?.()
      await flushPromises()
    },
  }
}

function setup(init: Parameters<typeof createFakeSource>[0] = {}, sleep: (ms: number) => Promise<void> = async () => undefined) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' }],
    turns: { [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z')] },
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep })
  return { ...fake, session }
}

const timedOut = () => new ConversationRequestError('timeout of 30000ms exceeded', null, null)

describe('run adoption on a slow server (flowgate.v02.0084 T#2)', () => {
  it('keeps tracking a 409-adopted run through 404s until the server has it', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({ runs: { 'run-x': ['running', 'finished'] } }, clock.sleep)
    data.replies['run-x'] = { chatId: A, turn: turn(2, 'ai', 'reply', '2026-10-06T08:10:00Z') }
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(Object.assign(httpError(409, 'busy', 'run_in_progress'), { runId: 'run-x' }))
    source.fetchRun.mockRejectedValueOnce(httpError(404, 'gone')).mockRejectedValueOnce(httpError(404, 'gone'))
    await session.invokeAi()

    await clock.tick()
    await clock.tick()
    // Not registered yet: still the running run, no failure shown.
    expect(source.fetchRun).toHaveBeenCalledTimes(2)
    expect(session.state.runId).toBe('run-x')
    expect(session.state.notice).toBeNull()

    await clock.tick()
    expect(session.state.runId).toBe('run-x')
    await clock.tick()
    expect(session.state.runId).toBeNull()
    expect(session.state.turns.at(-1)?.body).toBe('reply')
    const statuses = session.state.activities.filter((a) => a.kind === 'status')
    expect(statuses).toEqual([])
  })

  it('adopts the active run after a /start without an answer, and fails only when there is none', async () => {
    const { session, source, data } = setup({ runs: { 'run-late': ['running', 'finished'] } })
    await session.setScope(SCOPE)
    await flushPromises()
    const opened = source.fetchActiveChatRun.mock.calls.length

    // The re-check is in flight: the button stays running and a second click sends nothing.
    let answer: (runId: string | null) => void = () => undefined
    source.startChatRun.mockRejectedValueOnce(timedOut())
    source.fetchActiveChatRun.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    const first = session.invokeAi()
    await flushPromises()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(opened + 1)
    expect(session.state.runId).toBe('starting')
    await session.invokeAi()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    answer('run-late')
    await first
    await flushPromises()
    expect(source.fetchRun).toHaveBeenCalledWith('run-late')
    expect(session.state.runId).toBeNull()
    expect(session.state.notice).toBeNull()

    // No active run: the failure is shown as before.
    data.activeRun = null
    source.startChatRun.mockRejectedValueOnce(timedOut())
    await session.invokeAi()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(opened + 2)
    expect(session.state.runId).toBeNull()
    expect(session.state.notice).toMatchObject({ code: 'invoke_failed', detail: 'timeout of 30000ms exceeded' })

    // A 4xx decision is not re-checked.
    source.startChatRun.mockRejectedValueOnce(httpError(422, 'bad request'))
    await session.invokeAi()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(opened + 2)
    expect(session.state.notice).toMatchObject({ code: 'invoke_failed', detail: 'bad request' })
  })

  it('ends at a 404 once the run was read, and lets a never-read run go after the grace', async () => {
    const seen = setup({ runs: { 'run-1': ['running'] } })
    await seen.session.setScope(SCOPE)
    await flushPromises()
    seen.source.startChatRun.mockResolvedValueOnce('run-1')
    seen.source.fetchRun.mockImplementation(async () => {
      if (seen.source.fetchRun.mock.calls.length > 1) throw httpError(404, 'gone')
      return { runId: 'run-1', status: 'running', endReason: null }
    })
    await seen.session.invokeAi()
    await flushPromises()
    expect(seen.source.fetchRun).toHaveBeenCalledTimes(2)
    expect(seen.session.state.runId).toBeNull()

    const never = setup()
    await never.session.setScope(SCOPE)
    await flushPromises()
    never.source.startChatRun.mockResolvedValueOnce('run-2')
    never.source.fetchRun.mockRejectedValue(httpError(404, 'gone'))
    await never.session.invokeAi()
    await flushPromises()
    expect(never.source.fetchRun).toHaveBeenCalledTimes(Math.ceil(RUN_ADOPT_GRACE_MS / RUN_POLL_MS))
    expect(never.session.state.runId).toBeNull()
  })
})
