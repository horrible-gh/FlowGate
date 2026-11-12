import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { ConversationRequestError } from '@/features/conversation/api'
import { RUN_ADOPT_GRACE_MS, RUN_POLL_MS, START_RESEND_MAX, createConversationSession } from '@/features/conversation/conversationSession'
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

  // flowgate.v02.0152: the first call of a chat ran past the 30s wait while the server went on to run the AI; the
  // screen said "AI를 호출하지 못했습니다" and a second press could run the same message again. Now the press is sent
  // again with the same client_request_id, and the server answers with the run it already admitted.
  it.each([['running', 'finished'], ['finished']] as const)(
    'sends the same press again after a /start without an answer and follows the run the server admitted (%s)',
    async (...statuses) => {
      const { session, source, data } = setup({ runs: { 'run-late': [...statuses] } })
      data.replies['run-late'] = { chatId: A, turn: turn(2, 'ai', 'reply', '2026-10-06T08:10:00Z') }
      await session.setScope(SCOPE)
      await flushPromises()
      // The server admitted the press (and may already have finished it), but its answer never arrived.
      source.startChatRun.mockImplementationOnce(async (_chat: string, _provider: string | null, key?: string) => {
        data.admitted[key!] = 'run-late'
        throw timedOut()
      })
      await session.invokeAi()
      await flushPromises()
      expect(source.startChatRun).toHaveBeenCalledTimes(2)
      const [first, resend] = source.startChatRun.mock.calls
      expect(first[2]).toEqual(expect.any(String))
      expect(resend[2]).toBe(first[2])
      expect(source.fetchRun).toHaveBeenCalledWith('run-late')
      // Exactly one run, its one reply, no failure.
      expect(Object.keys(data.runs)).toEqual(['run-late'])
      expect(session.state.turns.filter((t) => t.speaker === 'ai').map((t) => t.body)).toEqual(['reply'])
      expect(session.state.runId).toBeNull()
      expect(session.state.notice).toBeNull()
    },
  )

  it('waits while the server is still admitting the same press, with the button locked', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({}, clock.sleep)
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(httpError(409, 'still starting', 'start_pending'))
    const first = session.invokeAi()
    await flushPromises()
    expect(session.state.runId).toBe('starting')
    expect(clock.sleep).toHaveBeenLastCalledWith(RUN_POLL_MS)
    await session.invokeAi() // a second click during recovery sends nothing
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    await clock.tick()
    await first
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(source.startChatRun.mock.calls[1][2]).toBe(source.startChatRun.mock.calls[0][2])
    expect(session.state.runId).toBe('run-1')
    expect(Object.keys(data.admitted)).toHaveLength(1)
    expect(session.state.notice).toBeNull()
  })

  it('stops resending after START_RESEND_MAX, then adopts an active run or shows the failure', async () => {
    const lost = setup({ runs: { 'run-late': ['running', 'finished'] } })
    await lost.session.setScope(SCOPE)
    await flushPromises()
    const opened = lost.source.fetchActiveChatRun.mock.calls.length
    lost.source.startChatRun.mockRejectedValue(timedOut())
    lost.data.activeRun = 'run-late'
    await lost.session.invokeAi()
    await flushPromises()
    expect(lost.source.startChatRun).toHaveBeenCalledTimes(1 + START_RESEND_MAX)
    expect(new Set(lost.source.startChatRun.mock.calls.map((call: unknown[]) => call[2])).size).toBe(1)
    expect(lost.source.fetchActiveChatRun).toHaveBeenCalledTimes(opened + 1)
    expect(lost.source.fetchRun).toHaveBeenCalledWith('run-late')
    expect(lost.session.state.notice).toBeNull()

    const none = setup()
    await none.session.setScope(SCOPE)
    await flushPromises()
    none.source.startChatRun.mockRejectedValue(timedOut())
    await none.session.invokeAi()
    expect(none.source.startChatRun).toHaveBeenCalledTimes(1 + START_RESEND_MAX)
    expect(none.session.state.runId).toBeNull()
    // flowgate.v02test.0024 B: a line of the chat's history; no answer, so this browser keeps it (not the server).
    expect(none.session.state.notice).toBeNull()
    expect(none.session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([
      { code: 'invoke_failed', text: 'timeout of 30000ms exceeded', chatError: true }])
    expect(none.source.recordChatError).not.toHaveBeenCalled()
    expect(Object.keys(localStorage).some((key) => key.includes('.chat.errors.'))).toBe(true)
    localStorage.clear()
  })

  it('neither resends nor re-checks a 4xx decision', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    const opened = source.fetchActiveChatRun.mock.calls.length
    source.startChatRun.mockRejectedValueOnce(httpError(422, 'bad request'))
    await session.invokeAi()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(opened)
    expect(session.state.runId).toBeNull()
    // flowgate.v02test.0024 B: a line of the chat's history, kept by the server (it answered).
    expect(session.state.notice).toBeNull()
    expect(session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([{ code: 'invoke_failed', text: 'bad request' }])
    expect(source.recordChatError).toHaveBeenCalledTimes(1)
  })

  it('names every press anew, so a deliberate second call is its own run', async () => {
    const { session, source, data } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    await session.invokeAi()
    await flushPromises()
    expect(session.state.runId).toBeNull()
    await session.invokeAi()
    await flushPromises()
    const keys = source.startChatRun.mock.calls.map((call: unknown[]) => call[2])
    expect(keys).toHaveLength(2)
    expect(keys[1]).not.toBe(keys[0])
    expect(Object.values(data.admitted).sort()).toEqual(['run-1', 'run-2'])
  })

  it('ends at a 404 once the run was read, and lets a never-read run go after the grace', async () => {
    const seen = setup({ runs: { 'run-1': ['running'] } })
    await seen.session.setScope(SCOPE)
    await flushPromises()
    seen.source.startChatRun.mockResolvedValueOnce('run-1')
    seen.source.fetchRun.mockImplementation(async () => {
      if (seen.source.fetchRun.mock.calls.length > 1) throw httpError(404, 'gone')
      return { runId: 'run-1', docRef: A, actionScope: 'chat', status: 'running', endReason: null, outcome: null, exitCode: null }
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
