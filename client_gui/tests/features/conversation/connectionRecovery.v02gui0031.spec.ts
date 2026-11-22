import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { CHAT_EVENT_COALESCE_MS, type ChatEventSubscription, type SubscribeChatEvents } from '@/features/conversation/conversationEvents'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// ②③ — after a connection failure the open chat came back only with F5:
// ② the browser going online again recovered the chat list only (connection.restored), never the open chat's turns,
//    run and history — only a stream reconnect did;
// ③ a chat whose first read failed (turnsStatus 'error') was skipped by every automatic recovery (session.recover
//    returned for anything not 'ready', the panel's return to view too) and waited for its [다시 시도].
// Now online recovers the open chat like a reconnect, and a failed chat is opened again through its [다시 시도] path
// on online, a reconnect and a return to the panel. 0060: a message cut off by the outage (no answer, 5xx) is sent again
// by itself once the connection is back, under its own idempotency key; nothing else is sent.
registerAppMessages(i18n)

const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
let wrapper: VueWrapper | null = null
let visibility: DocumentVisibilityState = 'visible'
const sessions: ConversationSession[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  for (const session of sessions.splice(0)) session.dispose()
  delete (document as unknown as Record<string, unknown>).visibilityState
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function fakeStream() {
  const subs = new Set<ChatEventSubscription>()
  const subscribe: SubscribeChatEvents = vi.fn((_projectId: string, sub: ChatEventSubscription) => {
    subs.add(sub)
    return () => subs.delete(sub)
  })
  return {
    subscribe,
    reconnect() {
      for (const sub of [...subs]) sub.resync?.()
    },
  }
}

const coalesce = () => new Promise((done) => setTimeout(done, CHAT_EVENT_COALESCE_MS + 30))
const bodies = (session: ConversationSession) =>
  session.timeline.value.filter((item) => item.kind === 'message').map((item) => (item.kind === 'message' ? item.turn.body : ''))

async function mountPanel(firstReadFails: boolean) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'hello'), turn(2, 'ai', 'hi')], [B]: [turn(1, 'user', 'other')] },
  })
  if (firstReadFails) fake.source.fetchTurns.mockRejectedValueOnce(httpError(503, 'Service Unavailable'))
  const stream = fakeStream()
  wrapper = mount(ConversationPanel, {
    props: { projectId: SCOPE.projectId, groupId: SCOPE.groupId, source: fake.source, subscribeEvents: stream.subscribe, active: true },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await flushPromises()
  const session = (wrapper.vm as unknown as { session: ConversationSession }).session
  expect(session.state.activeChatId).toBe(A)
  expect(session.state.turnsStatus).toBe(firstReadFails ? 'error' : 'ready')
  return { ...fake, session, stream }
}

describe('v02gui.0031 T0005 ② online recovers the open chat', () => {
  it('recovers the screen after an outage and retries the failed message without F5 or an SSE reconnect', async () => {
    const { session, source, data } = await mountPanel(false)
    source.postTurn.mockRejectedValueOnce(httpError(503, 'database unavailable'))
      .mockRejectedValueOnce(httpError(503, 'database unavailable'))
      .mockRejectedValueOnce(httpError(503, 'database unavailable'))
    expect(await session.send('held during outage')).toBe(false)
    const failed = session.state.pending[0]
    expect(wrapper!.get('[data-test="tl-failure"]').text()).toContain('database unavailable')
    data.turns[A].push(turn(3, 'ai', 'arrived during outage'))
    window.dispatchEvent(new Event('online'))
    await coalesce()
    await flushPromises()
    expect(bodies(session)).toContain('arrived during outage')
    // 0060: sent again by itself on online — no [다시 시도] — under the same key.
    expect(source.postTurn).toHaveBeenCalledTimes(4)
    expect(source.postTurn.mock.calls[3][1].idempotencyKey).toBe(failed.idempotencyKey)
    expect(session.state.pending).toHaveLength(0)
    expect(data.turns[A].filter(t => t.body === 'held during outage')).toHaveLength(1)
  })

  it('online reads the open chat again (turns, active run, activities, history) without a stream reconnect', async () => {
    const { session, source, data } = await mountPanel(false)
    const before = { turns: source.fetchTurns.mock.calls.length, active: source.fetchActiveChatRun.mock.calls.length,
      activities: source.listActivities.mock.calls.length }
    data.turns[A].push(turn(3, 'ai', 'stored while offline'))

    window.dispatchEvent(new Event('online'))
    await coalesce()
    await flushPromises()

    expect(bodies(session)).toEqual(['hello', 'hi', 'stored while offline'])
    expect(source.fetchTurns.mock.calls.length).toBeGreaterThan(before.turns)
    expect(source.fetchActiveChatRun.mock.calls.length).toBeGreaterThan(before.active)
    expect(source.listActivities.mock.calls.length).toBeGreaterThan(before.activities)
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it('online and a reconnect together are one recovery', async () => {
    const { source, stream } = await mountPanel(false)
    const before = source.fetchActiveChatRun.mock.calls.length
    window.dispatchEvent(new Event('online'))
    stream.reconnect()
    await coalesce()
    await flushPromises()
    expect(source.fetchActiveChatRun.mock.calls.length).toBe(before + 1)
  })

  it('a recovery raised for one chat is dropped when another chat opens first (generation kept)', async () => {
    const { session, source } = await mountPanel(false)
    window.dispatchEvent(new Event('online'))
    await session.selectChat(B)
    const afterSwitch = source.fetchTurns.mock.calls.length
    await coalesce()
    await flushPromises()
    expect(source.fetchTurns.mock.calls.slice(afterSwitch).map((call) => call[0])).not.toContain(A)
    expect(session.state.activeChatId).toBe(B)
  })
})

describe('v02gui.0031 T0005 ③ a chat whose first read failed recovers by itself', () => {
  it('on online', async () => {
    const { session, source } = await mountPanel(true)
    window.dispatchEvent(new Event('online'))
    await coalesce()
    await flushPromises()
    expect(session.state.turnsStatus).toBe('ready')
    expect(bodies(session)).toEqual(['hello', 'hi'])
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it('on a stream reconnect', async () => {
    const { session, stream } = await mountPanel(true)
    stream.reconnect()
    await coalesce()
    await flushPromises()
    expect(session.state.turnsStatus).toBe('ready')
  })

  it('on a return to the panel (tab shown again), not while hidden', async () => {
    const { session } = await mountPanel(true)
    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(session.state.turnsStatus).toBe('error')

    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    await flushPromises()
    expect(session.state.turnsStatus).toBe('ready')
  })

  it('stays failed (and retryable) when the read fails again; the next signal tries again', async () => {
    const { session, source } = await mountPanel(true)
    source.fetchTurns.mockRejectedValueOnce(httpError(502, 'Bad Gateway'))
    window.dispatchEvent(new Event('online'))
    await coalesce()
    await flushPromises()
    expect(session.state.turnsStatus).toBe('error')

    window.dispatchEvent(new Event('online'))
    await coalesce()
    await flushPromises()
    expect(session.state.turnsStatus).toBe('ready')
  })
})

describe('v02gui.0031 T0005 ③ session.recover on a failed chat', () => {
  async function failedSession() {
    const fake = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'hello')] },
    })
    fake.source.fetchTurns.mockRejectedValueOnce(httpError(504, 'Gateway Timeout'))
    const session = createConversationSession({ source: fake.source })
    sessions.push(session)
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.turnsStatus).toBe('error')
    return { ...fake, session }
  }

  it('opens the chat again through the [다시 시도] path, and signals meanwhile become one round after it', async () => {
    const { session, source } = await failedSession()
    let release!: () => void
    const gate = new Promise<void>((done) => { release = done })
    const real = source.fetchTurns.getMockImplementation()!
    source.fetchTurns.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await gate
      return real(...args)
    })
    const calls = source.fetchTurns.mock.calls.length

    const first = session.recover()
    expect(session.state.turnsStatus).toBe('loading')
    void session.recover({ turns: true })
    void session.recover()
    expect(source.fetchTurns.mock.calls.length).toBe(calls + 1) // the held signals read nothing beside it

    release()
    await first
    await flushPromises()
    expect(session.state.turnsStatus).toBe('ready')
    expect(bodies(session)).toEqual(['hello'])
    expect(source.postTurn).not.toHaveBeenCalled()
  })
})
