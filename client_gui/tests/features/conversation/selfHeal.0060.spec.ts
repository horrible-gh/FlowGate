import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { setConnectionLost } from '@shared/connectionStatus'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import type { ConversationSession } from '@/features/conversation/conversationSession'
import type { ChatEventSubscription, SubscribeChatEvents } from '@/features/conversation/conversationEvents'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// 0060 (13:22 again): the chat list and the open chat came back only when a recovery signal reached them (the event
// stream reconnecting, fg:connection_recovered, online, a return to the tab). When none came — the stream never
// reopened, the recovery loop had already ended — they stayed failed until F5. Now they read again by themselves
// while failed, a list that failed as the Flow opened opens its newest chat once it loads, and a message cut off by
// the outage is sent again by itself under its own idempotency key once the server answers.
registerAppMessages(i18n)

const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  // Only the timers: flushPromises keeps its own scheduler.
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setConnectionLost(false)
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// A stream that never reconnects: no resync ever reaches the panel.
const silentStream: SubscribeChatEvents = vi.fn((_projectId: string, _sub: ChatEventSubscription) => () => {})

async function mountPanel(setup: (fake: ReturnType<typeof createFakeSource>) => void) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'hello'), turn(2, 'ai', 'hi')], [B]: [turn(1, 'user', 'other')] },
  })
  setup(fake)
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, subscribeEvents: silentStream, active: true },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await flushPromises()
  const session = (wrapper.vm as unknown as { session: ConversationSession }).session
  return { ...fake, session }
}

async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
  await flushPromises()
  await flushPromises()
}

describe('0060 the conversation panel heals itself', () => {
  it('a chat list that failed as the Flow opened reads again after 5 s with no signal and opens the newest chat', async () => {
    const { source, session } = await mountPanel((fake) => {
      fake.source.listChats.mockRejectedValueOnce(httpError(503, 'database unavailable'))
        .mockRejectedValueOnce(httpError(503, 'database unavailable'))
    })
    expect(session.state.chatsStatus).toBe('error')
    expect(session.state.activeChatId).toBeNull()

    await advance(5000)
    expect(source.listChats).toHaveBeenCalledTimes(2)
    expect(session.state.chatsStatus).toBe('error')

    await advance(10000)
    expect(source.listChats).toHaveBeenCalledTimes(3)
    expect(session.state.chatsStatus).toBe('ready')
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.turnsStatus).toBe('ready')

    // Healthy: nothing more is read.
    await advance(5 * 60_000)
    expect(source.listChats).toHaveBeenCalledTimes(3)
  })

  it('an open chat whose read failed is opened again after 5 s with no signal', async () => {
    const { source, session } = await mountPanel((fake) => {
      fake.source.fetchTurns.mockRejectedValueOnce(httpError(503, 'Service Unavailable'))
    })
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.turnsStatus).toBe('error')
    await advance(5000)
    expect(session.state.turnsStatus).toBe('ready')
    expect(source.fetchTurns.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('while the server is unreachable the panel says it is reconnecting instead of showing the list failure', async () => {
    await mountPanel((fake) => {
      fake.source.listChats.mockRejectedValue(httpError(503, 'database unavailable'))
    })
    expect(wrapper!.text()).toContain('채팅 목록을 불러오지 못했습니다')
    setConnectionLost(true)
    await flushPromises()
    expect(wrapper!.text()).not.toContain('채팅 목록을 불러오지 못했습니다')
    expect(wrapper!.text()).toContain('다시 연결하는 중')
  })

  it('a message cut off by the outage is sent again by itself when the connection is back, under the same key', async () => {
    const { source, session, data } = await mountPanel(() => {})
    expect(session.state.turnsStatus).toBe('ready')
    const real = source.postTurn.getMockImplementation()!
    source.postTurn.mockRejectedValue(httpError(503, 'database unavailable'))
    const sending = session.send('typed during outage')
    await advance(60_000)
    expect(await sending).toBe(false)
    const failed = session.state.pending[0]
    expect(failed.failed).toBe(true)
    const tries = source.postTurn.mock.calls.length
    expect(tries).toBeGreaterThan(0)

    // The server is back; nobody presses [다시 시도].
    source.postTurn.mockImplementation(real)
    window.dispatchEvent(new Event('fg:connection_recovered'))
    await advance(1000)

    expect(source.postTurn).toHaveBeenCalledTimes(tries + 1)
    expect(source.postTurn.mock.calls[tries][1].idempotencyKey).toBe(failed.idempotencyKey)
    expect(session.state.pending).toHaveLength(0)
    expect(data.turns[A].filter((t) => t.body === 'typed during outage')).toHaveLength(1)
  })

  it('a refused message (422) is not sent again by itself', async () => {
    const { source, session } = await mountPanel(() => {})
    source.postTurn.mockRejectedValue(httpError(422, 'refused'))
    const sending = session.send('refused message')
    await advance(60_000)
    expect(await sending).toBe(false)
    const calls = source.postTurn.mock.calls.length
    window.dispatchEvent(new Event('fg:connection_recovered'))
    window.dispatchEvent(new Event('online'))
    await advance(60_000)
    expect(source.postTurn).toHaveBeenCalledTimes(calls)
    expect(session.state.pending[0]?.failed).toBe(true)
  })
})
