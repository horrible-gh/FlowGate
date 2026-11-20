import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { notifyGetRetry } from '@shared/requestRecovery'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import type { ConversationSession } from '@/features/conversation/conversationSession'
import type { ChatEventSubscription, SubscribeChatEvents } from '@/features/conversation/conversationEvents'
import { createFakeSource, httpError } from './fakeConversationSource'

// flowgate.v02gui.0017 B3: a chat list whose read failed (a dropped connection, a server restart) stayed on
// "채팅 목록을 불러오지 못했습니다" until [다시 시도] was pressed — the open-chat recovery (SSE resync, revisit) does
// not cover the list, and with the list failed no chat is open. Now the list reads again by itself when the event
// stream reconnects, when the browser is online again and when the tab is shown again; a list that loaded is left alone.
registerAppMessages(i18n)

const A = 'p.m.0001.0002-CH'
let wrapper: VueWrapper | null = null
let visibility: DocumentVisibilityState = 'visible'

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  delete (document as unknown as Record<string, unknown>).visibilityState
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
    async reconnect() {
      for (const sub of [...subs]) sub.resync?.()
      await flushPromises()
    },
    get size() {
      return subs.size
    },
  }
}

async function mountWithFailedList() {
  const fake = createFakeSource({ chats: [{ id: A, title: 'A', updatedAt: null }] })
  fake.source.listChats.mockRejectedValueOnce(httpError(503, 'Service Unavailable'))
  const stream = fakeStream()
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, subscribeEvents: stream.subscribe },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await flushPromises()
  const session = (wrapper.vm as unknown as { session: ConversationSession }).session
  expect(session.state.chatsStatus).toBe('error')
  expect(fake.source.listChats).toHaveBeenCalledTimes(1)
  return { ...fake, session, stream }
}

describe('v02gui.0017 B3 — the failed chat list recovers by itself', () => {
  it('remembers recovery while loading and merges repeated signals into one retry', async () => {
    const { source, session, stream } = await mountWithFailedList()
    let reject!: (error: unknown) => void
    source.listChats.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    const loading = session.recoverChatList()
    await stream.reconnect()
    await stream.reconnect()
    expect(source.listChats).toHaveBeenCalledTimes(2)
    reject(httpError(503, 'unavailable'))
    await loading
    expect(source.listChats).toHaveBeenCalledTimes(3)
    expect(session.state.chatsStatus).toBe('ready')
  })

  it('consumes pending recovery even when that one retry also fails', async () => {
    const { source, session, stream } = await mountWithFailedList()
    let reject!: (error: unknown) => void
    source.listChats.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    source.listChats.mockRejectedValueOnce(httpError(503, 'still unavailable'))
    const loading = session.recoverChatList()
    await stream.reconnect()
    reject(httpError(503, 'unavailable'))
    await loading
    expect(source.listChats).toHaveBeenCalledTimes(3)
    expect(session.state.chatsStatus).toBe('error')
  })

  it('does not retry a successful read just because recovery arrived during loading', async () => {
    const { source, session, stream } = await mountWithFailedList()
    let resolve!: (chats: Awaited<ReturnType<typeof source.listChats>>) => void
    source.listChats.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const loading = session.recoverChatList()
    await stream.reconnect()
    notifyGetRetry('/api/v1/documents', { project_id: 'other', group_id: 'other', type_code: 'CH' })
    await flushPromises()
    expect(wrapper!.text()).not.toContain('다시 연결하는 중')
    notifyGetRetry('/api/v1/documents', { project_id: 'p', group_id: 'p.m.0001', type_code: 'CH' })
    await flushPromises()
    expect(wrapper!.text()).toContain('다시 연결하는 중')
    resolve([])
    await loading
    await flushPromises()
    expect(wrapper!.text()).not.toContain('다시 연결하는 중')
    expect(source.listChats).toHaveBeenCalledTimes(2)
    expect(session.state.chatsStatus).toBe('ready')
  })

  it('reads the list again when the event stream reconnects', async () => {
    const { source, session, stream } = await mountWithFailedList()
    expect(stream.size).toBeGreaterThan(0) // subscribed although no chat is open

    await stream.reconnect()

    expect(source.listChats).toHaveBeenCalledTimes(2)
    expect(session.state.chatsStatus).toBe('ready')
    expect(session.state.chats.map((c) => c.id)).toEqual([A])
  })

  it('reads the list again when the browser is online again', async () => {
    const { source, session } = await mountWithFailedList()

    window.dispatchEvent(new Event('online'))
    await flushPromises()

    expect(source.listChats).toHaveBeenCalledTimes(2)
    expect(session.state.chatsStatus).toBe('ready')
  })

  it('reads the list again when the tab is shown again, not while hidden', async () => {
    const { source, session } = await mountWithFailedList()

    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledTimes(1)

    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledTimes(2)
    expect(session.state.chatsStatus).toBe('ready')
  })

  it('leaves a loaded list alone on the same signals', async () => {
    const { source, stream } = await mountWithFailedList()
    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledTimes(2)

    window.dispatchEvent(new Event('online'))
    await stream.reconnect()
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledTimes(2)
  })

  it('stops listening once the panel is gone', async () => {
    const { source, stream } = await mountWithFailedList()
    wrapper!.unmount()
    wrapper = null
    expect(stream.size).toBe(0)

    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledTimes(1)
  })
})
