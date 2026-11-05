import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { ConversationRequestError } from '@/features/conversation/api'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession } from '@/features/conversation/conversationSession'
import type { ChatSummary } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0135 T#2 — the conversation notice line belongs to the Flow and the chat (or the
// draft) it was raised for. B0001 §1.C: a "새 채팅을 만들지 못했습니다" raised in one Flow stayed on
// top of another Flow's open chat, because the session-wide notice was never cleared on a Flow
// change and late answers of the previous Flow could still raise one.
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const FLOW_A = { projectId: 'p', groupId: 'p.m.0001' } // 테스트: no chat yet
const FLOW_B = { projectId: 'p', groupId: 'p.m.0002' } // 플로우 후속처리: one chat with history
const B1 = 'p.m.0002.0002-CH'
const B2 = 'p.m.0002.0003-CH'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}

/** A fake server with one chat list per Flow (the shared fake lists the same chats everywhere). */
function twoFlows() {
  const fake = createFakeSource({
    turns: { [B1]: [turn(1, 'user', '조사 완료 후 다음 단계 논의'), turn(2, 'ai', '네')], [B2]: [turn(1, 'user', '다른 채팅')] },
  })
  const chatsOf: Record<string, ChatSummary[]> = {
    [FLOW_A.groupId]: [],
    [FLOW_B.groupId]: [
      { id: B1, title: '조사 완료 후 다음 단계 논의', updatedAt: '2026-11-05T04:00:00Z' },
      { id: B2, title: '다른 채팅', updatedAt: '2026-11-05T03:00:00Z' },
    ],
  }
  let made = 0
  fake.source.listChats.mockImplementation(async (scope: { groupId: string }) => (chatsOf[scope.groupId] ?? []).map((c) => ({ ...c })))
  fake.source.createChat.mockImplementation(async (scope: { groupId: string }, title: string) => {
    made += 1
    const chat = { id: `${scope.groupId}.00${10 + made}-CH`, title, updatedAt: '2026-11-05T05:00:00Z' }
    chatsOf[scope.groupId].unshift(chat)
    fake.data.turns[chat.id] = []
    return { ...chat }
  })
  return { ...fake, chatsOf }
}

async function setup() {
  const fake = twoFlows()
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
  await session.setScope(FLOW_A)
  await flushPromises()
  return { ...fake, session, start }
}

describe('a notice stays with its Flow', () => {
  it('a create failure in Flow A is not shown on Flow B, nor after coming back, and A then creates normally', async () => {
    const { session, start, source, data, chatsOf } = await setup()
    expect(start.isDraft.value).toBe(true)
    source.createChat.mockRejectedValueOnce(httpError(500, 'Internal Server Error'))
    expect(await start.send('첫 메시지')).toBe(false)
    expect(session.state.notice?.code).toBe('create_failed')

    await session.setScope(FLOW_B)
    await flushPromises()
    expect(session.state.notice).toBeNull()
    expect(session.state.activeChatId).toBe(B1)
    expect(session.state.turns.map((t) => t.body)).toEqual(['조사 완료 후 다음 단계 논의', '네'])

    await session.setScope(FLOW_A)
    await flushPromises()
    expect(session.state.notice).toBeNull()
    expect(start.isDraft.value).toBe(true)

    expect(await start.send('첫 메시지')).toBe(true)
    await flushPromises()
    const created = chatsOf[FLOW_A.groupId][0].id
    expect(session.state.activeChatId).toBe(created)
    expect(data.turns[created].map((t) => t.body)).toEqual(['첫 메시지'])
    expect(session.state.notice).toBeNull()
    expect(source.postTurn).toHaveBeenCalledTimes(1) // the first message is stored once
    expect(chatsOf[FLOW_B.groupId].map((c) => c.id)).toEqual([B1, B2]) // Flow B untouched
  })

  it('in the same Flow a failure stays while its draft is open and goes with the next success', async () => {
    const { session, start, source } = await setup()
    source.createChat.mockRejectedValueOnce(httpError(500)).mockRejectedValueOnce(httpError(503))
    await start.send('hello')
    const first = session.state.notice
    expect(first?.code).toBe('create_failed')
    await start.send('hello')
    expect(session.state.notice?.code).toBe('create_failed')
    expect(session.state.notice?.id).not.toBe(first?.id) // the newest failure is the one shown
    expect(start.isDraft.value).toBe(true)
    expect(await start.send('hello')).toBe(true)
    await flushPromises()
    expect(session.state.notice).toBeNull()
  })

  it('picking an existing chat of the same Flow clears a draft failure; reopening the same chat keeps its own', async () => {
    const { session, start, source } = await setup()
    await session.setScope(FLOW_B)
    await flushPromises()
    start.startNew()
    source.createChat.mockRejectedValueOnce(httpError(500))
    await start.send('new one')
    expect(session.state.notice?.code).toBe('create_failed')
    await session.selectChat(B1)
    await flushPromises()
    expect(session.state.notice).toBeNull()

    source.postTurn.mockRejectedValueOnce(httpError(422, 'nope'))
    expect(await session.send('again')).toBe(false)
    expect(session.state.notice?.code).toBe('send_failed')
    await session.selectChat(B1) // same chat, already open: no reload, the notice is about it
    expect(session.state.notice?.code).toBe('send_failed')
    start.startNew()
    expect(session.state.notice).toBeNull()
  })
})

describe('late answers of a previous Flow or chat (scopeGen / chatGen races)', () => {
  it('a slow create of Flow A that fails after the move to Flow B touches nothing of B', async () => {
    const { session, start, source } = await setup()
    const slow = deferred<never>()
    source.createChat.mockImplementationOnce(() => slow.promise)
    const sending = start.send('first')
    await flushPromises()
    await session.setScope(FLOW_B)
    await flushPromises()
    slow.reject(httpError(500, 'Internal Server Error'))
    expect(await sending).toBe(false)
    await flushPromises()
    expect(session.state.notice).toBeNull()
    expect(session.state.scope).toEqual(FLOW_B)
    expect(session.state.chats.map((c) => c.id)).toEqual([B1, B2])
    expect(session.state.activeChatId).toBe(B1)
    expect(session.state.turns).toHaveLength(2)
  })

  it('a slow create of Flow A that succeeds after the move neither opens nor sends anything in B', async () => {
    const { session, start, source } = await setup()
    const slow = deferred<ChatSummary>()
    source.createChat.mockImplementationOnce(() => slow.promise)
    const sending = start.send('first')
    await flushPromises()
    await session.setScope(FLOW_B)
    await flushPromises()
    slow.resolve({ id: 'p.m.0001.0099-CH', title: '새 대화', updatedAt: '2026-11-05T05:00:00Z' })
    expect(await sending).toBe(false)
    await flushPromises()
    expect(session.state.chats.map((c) => c.id)).toEqual([B1, B2])
    expect(session.state.activeChatId).toBe(B1)
    expect(session.state.notice).toBeNull()
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it('a slow draft create that fails after an existing chat was picked raises nothing on that chat', async () => {
    const { session, start, source } = await setup()
    await session.setScope(FLOW_B)
    await flushPromises()
    start.startNew()
    const slow = deferred<never>()
    source.createChat.mockImplementationOnce(() => slow.promise)
    const sending = start.send('draft text')
    await flushPromises()
    await session.selectChat(B2)
    slow.reject(httpError(500))
    expect(await sending).toBe(false)
    await flushPromises()
    expect(session.state.activeChatId).toBe(B2)
    expect(session.state.notice).toBeNull()
  })

  it('late rename and mention failures of the previous chat or Flow stay silent; a current one still shows', async () => {
    const { session, source } = await setup()
    await session.setScope(FLOW_B)
    await flushPromises()

    const rename = deferred<never>()
    source.renameChat.mockImplementationOnce(() => rename.promise)
    const renaming = session.renameActiveChat('새 이름')
    await session.selectChat(B2)
    rename.reject(httpError(500))
    expect(await renaming).toBe(false)
    expect(session.state.notice).toBeNull()

    const mention = deferred<never>()
    source.issueChatMention.mockImplementationOnce(() => mention.promise)
    const issuing = session.issueMention()
    await session.setScope(FLOW_A)
    mention.reject(httpError(500))
    expect(await issuing).toBeNull()
    await flushPromises()
    expect(session.state.notice).toBeNull()

    await session.setScope(FLOW_B)
    await flushPromises()
    source.renameChat.mockRejectedValueOnce(httpError(500))
    expect(await session.renameActiveChat('다른 이름')).toBe(false)
    expect(session.state.notice?.code).toBe('rename_failed') // a failure on screen is not hidden
  })
})

describe('ConversationPanel: Flow switch and reload (F5)', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    localStorage.clear()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  const mountPanel = async (source: ReturnType<typeof twoFlows>['source'], groupId: string) => {
    wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId, source },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
    return wrapper
  }
  const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
  const findAll = (sel: string) => wrapper!.findAll(`[data-test="${sel}"]`)

  it('A fails → B shows its chat without the banner → back to A creates the chat → a remount starts clean', async () => {
    const { source, chatsOf, data } = twoFlows()
    source.createChat.mockRejectedValueOnce(httpError(500, 'Internal Server Error'))
    await mountPanel(source, FLOW_A.groupId)
    await find('conv-input').setValue('첫 메시지')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect(find('conv-notice').exists()).toBe(true)
    expect(find('conv-notice').text()).toContain(i18n.global.t('gui.conversation.notice.create_failed'))

    await wrapper!.setProps({ groupId: FLOW_B.groupId })
    await flushPromises()
    expect(find('conv-notice').exists()).toBe(false)
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['조사 완료 후 다음 단계 논의', '네'])

    await wrapper!.setProps({ groupId: FLOW_A.groupId })
    await flushPromises()
    expect(find('conv-notice').exists()).toBe(false)
    await find('conv-input').setValue('첫 메시지')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    const created = chatsOf[FLOW_A.groupId][0].id
    expect(data.turns[created].map((t) => t.body)).toEqual(['첫 메시지'])
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['첫 메시지'])
    expect(find('conv-notice').exists()).toBe(false)

    // F5: a new panel (new session) on Flow B shows no notice from before the reload.
    wrapper!.unmount()
    await mountPanel(source, FLOW_B.groupId)
    expect(find('conv-notice').exists()).toBe(false)
    expect(findAll('tl-message')).toHaveLength(2)
  })
})

describe('a chat create without an answer (flowgate.v02.0135 T#3)', () => {
  it('reads the Flow list again, so a chat the server made late shows instead of a second one being made', async () => {
    const { session, start, source, chatsOf } = await setup()
    source.createChat.mockImplementationOnce(async (scope: { groupId: string }, title: string) => {
      // The server stores the chat but the answer never arrives (stg: committed ~2 min later, the GUI gave up at 30 s).
      chatsOf[scope.groupId].unshift({ id: 'p.m.0001.0002-CH', title, updatedAt: '2026-11-05T05:55:55Z' })
      throw new ConversationRequestError('timeout of 30000ms exceeded', null, null)
    })
    const listed = source.listChats.mock.calls.length
    expect(await start.send('첫 메시지')).toBe(false)
    await flushPromises()
    expect(session.state.notice?.code).toBe('create_failed')
    expect(source.listChats.mock.calls.length).toBe(listed + 1)
    expect(session.state.chats.map((c) => c.id)).toEqual(['p.m.0001.0002-CH'])
    expect(source.postTurn).not.toHaveBeenCalled() // nothing is sent to a chat the user did not see made
  })

  it('a refused create (the server answered) does not read the list again', async () => {
    const { start, source } = await setup()
    source.createChat.mockRejectedValueOnce(httpError(500))
    const listed = source.listChats.mock.calls.length
    await start.send('첫 메시지')
    await flushPromises()
    expect(source.listChats.mock.calls.length).toBe(listed)
  })
})
