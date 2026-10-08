import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { ConversationRequestError } from '@/features/conversation/api'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession } from '@/features/conversation/conversationSession'
import type { ProviderList } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0047 — a chat started from the composer (R0001 §2.1, §2.2): created by its first
// send, never as an empty draft, and titled once by the AI unless the user named it.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const NEW_EMPTY_FLOW = 'p.m.0001.0010-CH'
const NEW_AFTER_A = 'p.m.0001.0011-CH'

async function setup(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource(init)
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session, start }
}

const withA = () => ({ chats: [{ id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' }], turns: { [A]: [turn(1, 'user', 'look')] } })

describe('first send in a Flow without chats', () => {
  it('is a draft at once and creates the chat with the message', async () => {
    const { session, start, source, data } = await setup()
    expect(start.isDraft.value).toBe(true)
    expect(source.createChat).not.toHaveBeenCalled()
    expect(await start.send('로그인 오류 원인을 찾아 줘')).toBe(true)
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledWith(SCOPE, '새 대화', { autoTitle: true })
    expect(source.postTurn).toHaveBeenCalledWith(NEW_EMPTY_FLOW, expect.objectContaining({ body: '로그인 오류 원인을 찾아 줘' }))
    expect(session.state.activeChatId).toBe(NEW_EMPTY_FLOW)
    expect(start.isDraft.value).toBe(false)
    expect(data.turns[NEW_EMPTY_FLOW].map((t) => t.body)).toEqual(['로그인 오류 원인을 찾아 줘'])
  })

  it('asks the AI title once, on the selected provider, after the first message', async () => {
    const { session, start, source } = await setup()
    session.selectProvider('sonnet')
    await start.send('로그인 오류 원인을 찾아 줘')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW_EMPTY_FLOW, 'sonnet')
    expect(session.activeChat.value?.title).toBe('AI: 로그인 오류 원인을 찾아 줘')
    await start.send('두 번째 메시지')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('does not hold the send for the title', async () => {
    const { session, start, source } = await setup()
    source.autoTitleChat.mockImplementation(() => new Promise(() => undefined))
    expect(await start.send('hello')).toBe(true)
    expect(session.state.sending).toBe(false)
    expect(start.starting.value).toBe(false)
    expect(session.activeChat.value?.title).toBe('새 대화')
  })
})

describe('the draft from [+]', () => {
  it('stores nothing until something is sent', async () => {
    const { session, start, source } = await setup(withA())
    expect(session.state.activeChatId).toBe(A)
    start.startNew()
    expect(start.isDraft.value).toBe(true)
    expect(session.state.turns).toEqual([])
    await session.selectChat(A)
    start.startNew()
    await flushPromises()
    expect(source.createChat).not.toHaveBeenCalled()
    expect(session.state.chats.map((c) => c.id)).toEqual([A])
  })

  it('a title typed on the draft is the chat title and no AI title is asked', async () => {
    const { session, start, source } = await setup(withA())
    start.startNew()
    expect(await start.rename('  빌드 정리  ')).toBe(true)
    expect(start.draftTitle.value).toBe('빌드 정리')
    expect(source.renameChat).not.toHaveBeenCalled()
    await start.send('빌드 로그 확인')
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledWith(SCOPE, '빌드 정리')
    expect(session.activeChat.value?.title).toBe('빌드 정리')
    expect(source.autoTitleChat).not.toHaveBeenCalled()
  })

  it('typing the default title back keeps the AI title', async () => {
    const { start, source } = await setup(withA())
    start.startNew()
    await start.rename('x')
    await start.rename('새 대화')
    expect(start.draftTitle.value).toBeNull()
    await start.send('hi')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('[+] again drops the typed draft title', async () => {
    const { start } = await setup(withA())
    start.startNew()
    await start.rename('임시')
    start.startNew()
    expect(start.draftTitle.value).toBeNull()
  })
})

describe('manual title protection', () => {
  it('a rename while the AI answers wins on screen', async () => {
    const { session, start, source } = await setup()
    let answer!: (value: { id: string; title: string; updatedAt: string | null }) => void
    source.autoTitleChat.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    await start.send('hello')
    await flushPromises()
    expect(await start.rename('내 제목')).toBe(true)
    expect(source.renameChat).toHaveBeenCalledWith(NEW_EMPTY_FLOW, '내 제목')
    answer({ id: NEW_EMPTY_FLOW, title: 'AI title', updatedAt: null })
    await flushPromises()
    expect(session.activeChat.value?.title).toBe('내 제목')
  })

  it('a chat renamed before its first message is accepted gets no AI title', async () => {
    const { session, start, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'nope'))
    expect(await start.send('hello')).toBe(false)
    await start.rename('직접 지은 제목')
    expect(await start.retry(session.state.pending[0].localId)).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect(session.activeChat.value?.title).toBe('직접 지은 제목')
  })
})

describe('failures (R0001 §2.1-5, §2.2-4)', () => {
  it('a failed create sends nothing, keeps the draft and owes no title', async () => {
    const { session, start, source } = await setup(withA())
    start.startNew()
    source.createChat.mockRejectedValueOnce(httpError(409, 'disposed', 'conversation_group_disposed'))
    expect(await start.send('hello')).toBe(false)
    expect(source.postTurn).not.toHaveBeenCalled()
    expect(start.isDraft.value).toBe(true)
    expect(session.state.notice?.code).toBe('create_group_disposed')
    expect(source.autoTitleChat).not.toHaveBeenCalled()
  })

  it('a failed first send keeps the bubble; its accepted retry asks the title', async () => {
    const { session, start, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'nope'))
    expect(await start.send('hello')).toBe(false)
    expect(session.state.activeChatId).toBe(NEW_EMPTY_FLOW)
    const [bubble] = session.state.pending
    expect(bubble).toMatchObject({ body: 'hello', failed: true })
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect(await start.retry(bubble.localId)).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW_EMPTY_FLOW, 'opus')
  })

  it('an AI title that fails leaves the chat and its message alone', async () => {
    const { session, start, source } = await setup()
    source.autoTitleChat.mockRejectedValueOnce(httpError(502, 'no answer', 'conversation_auto_title_failed'))
    expect(await start.send('hello')).toBe(true)
    await flushPromises()
    expect(session.activeChat.value?.title).toBe('새 대화')
    expect(session.state.turns.map((t) => t.body)).toEqual(['hello'])
    expect(session.state.notice).toBeNull()
  })

  it('an AI title the server wrote after the request gave up still shows (rev3)', async () => {
    const { session, start, source, data } = await setup()
    // The model answered later than the request waited: the server stored the title, the
    // client only saw a timeout.
    source.autoTitleChat.mockImplementationOnce(async (chatId: string) => {
      data.chats = data.chats.map((c) => (c.id === chatId ? { ...c, title: '로그인 오류 조사' } : c))
      throw new ConversationRequestError('timeout of 130000ms exceeded', null, null)
    })
    const listed = source.listChats.mock.calls.length
    expect(await start.send('로그인 오류 원인을 찾아 줘')).toBe(true)
    await flushPromises()
    expect(source.listChats.mock.calls.length).toBeGreaterThan(listed + 1)
    expect(session.activeChat.value?.title).toBe('로그인 오류 조사')
    expect(session.state.notice).toBeNull()
  })

  it('an AI title the server refused does not read the list again', async () => {
    const { start, source } = await setup()
    source.autoTitleChat.mockRejectedValueOnce(httpError(409, 'changed', 'conversation_title_changed'))
    const listed = source.listChats.mock.calls.length
    await start.send('hello')
    await flushPromises()
    // Only the read that opens the created chat.
    expect(source.listChats.mock.calls.length).toBe(listed + 1)
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('without an AI to ask, no title is requested', async () => {
    const fake = createFakeSource()
    fake.source.listProviders.mockResolvedValue({ providers: [], defaultProviderId: null })
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await start.send('hello')).toBe(true)
    await flushPromises()
    expect(fake.source.autoTitleChat).not.toHaveBeenCalled()
    expect(session.activeChat.value?.title).toBe('새 대화')
  })
})

describe('a provider list still loading at the first send (R0001 §2.2-1)', () => {
  function setupSlowProviders() {
    const fake = createFakeSource()
    let resolve!: (list: ProviderList) => void
    let reject!: (error: unknown) => void
    fake.source.listProviders.mockImplementationOnce(() => new Promise<ProviderList>((res, rej) => ((resolve = res), (reject = rej))))
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
    return { ...fake, session, start, resolve: (list: ProviderList) => resolve(list), reject: (error: unknown) => reject(error) }
  }

  it('asks the title once the list arrives', async () => {
    const { session, start, source, resolve } = setupSlowProviders()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.providersStatus).toBe('loading')
    expect(await start.send('로그인 오류 원인을 찾아 줘')).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    resolve({ providers: [{ id: 'opus', name: 'Opus 5' }, { id: 'sonnet', name: 'Sonnet 5' }], defaultProviderId: 'sonnet' })
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW_EMPTY_FLOW, 'sonnet')
    expect(session.activeChat.value?.title).toBe('AI: 로그인 오류 원인을 찾아 줘')
    await start.send('두 번째')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('a list that fails to load asks nothing and keeps the chat', async () => {
    const { session, start, source, reject } = setupSlowProviders()
    await session.setScope(SCOPE)
    expect(await start.send('hello')).toBe(true)
    reject(httpError(500, 'down'))
    await flushPromises()
    expect(session.state.providersStatus).toBe('error')
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect(session.activeChat.value?.title).toBe('새 대화')
    expect(session.state.turns.map((t) => t.body)).toEqual(['hello'])
  })

  it('a rename made while the list loads wins', async () => {
    const { session, start, source, resolve } = setupSlowProviders()
    await session.setScope(SCOPE)
    await start.send('hello')
    await flushPromises()
    await start.rename('내 제목')
    resolve({ providers: [{ id: 'opus', name: 'Opus 5' }], defaultProviderId: 'opus' })
    await flushPromises()
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect(session.activeChat.value?.title).toBe('내 제목')
  })
})

describe('leaving the chat while its first message posts (R0001 §2.2-1)', () => {
  /** Holds the next postTurn until `release()`; it is stored then, as the server would. */
  function holdNextPost(source: ReturnType<typeof createFakeSource>['source']) {
    const original = source.postTurn.getMockImplementation()!
    let release!: () => void
    source.postTurn.mockImplementationOnce((chatId: string, input: { body: string }) => new Promise((resolve) => (release = () => resolve(original(chatId, input)))))
    return () => release()
  }

  it('for another chat of the list: the new chat still gets its AI title', async () => {
    const { session, start, source, data } = await setup(withA())
    start.startNew()
    const release = holdNextPost(source)
    const sent = start.send('로그인 오류 원인을 찾아 줘')
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledWith(NEW_AFTER_A, expect.objectContaining({ body: '로그인 오류 원인을 찾아 줘' }))
    await session.selectChat(A)
    release()
    expect(await sent).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW_AFTER_A, 'opus')
    expect(data.chats.find((c) => c.id === NEW_AFTER_A)?.title).toBe('AI: 로그인 오류 원인을 찾아 줘')
    expect(session.state.chats.find((c) => c.id === NEW_AFTER_A)?.title).toBe('AI: 로그인 오류 원인을 찾아 줘')
    expect(session.state.activeChatId).toBe(A)
    expect(session.activeChat.value?.title).toBe('Auth bug')
  })

  it('for a new draft from [+]: the chat left behind gets its AI title once', async () => {
    const { session, start, source } = await setup()
    const release = holdNextPost(source)
    const sent = start.send('hello')
    await flushPromises()
    start.startNew()
    expect(start.isDraft.value).toBe(true)
    release()
    expect(await sent).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW_EMPTY_FLOW, 'opus')
    expect(session.state.chats.find((c) => c.id === NEW_EMPTY_FLOW)?.title).toBe('AI: hello')
    expect(start.isDraft.value).toBe(true)
    await session.selectChat(NEW_EMPTY_FLOW)
    await start.send('두 번째')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('a chat whose first send failed stays owed while another chat is started', async () => {
    const { session, start, source } = await setup(withA())
    start.startNew()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'nope'))
    expect(await start.send('hello')).toBe(false)
    const left = session.state.activeChatId!
    expect(left).toBe(NEW_AFTER_A)
    start.startNew()
    expect(await start.send('other')).toBe(true)
    await flushPromises()
    const other = session.state.activeChatId!
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.autoTitleChat).toHaveBeenLastCalledWith(other, 'opus')
    await session.selectChat(left)
    expect(await start.send('again')).toBe(true)
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(2)
    expect(source.autoTitleChat).toHaveBeenLastCalledWith(left, 'opus')
    expect(session.activeChat.value?.title).toBe('AI: again')
  })
})

describe('an open chat', () => {
  it('sends and renames through the session as before, with no title request', async () => {
    const { session, start, source } = await setup(withA())
    expect(start.isDraft.value).toBe(false)
    expect(await start.send('more')).toBe(true)
    await start.rename('Auth bug 2')
    await flushPromises()
    expect(source.createChat).not.toHaveBeenCalled()
    expect(source.postTurn).toHaveBeenCalledWith(A, expect.objectContaining({ body: 'more' }))
    expect(source.renameChat).toHaveBeenCalledWith(A, 'Auth bug 2')
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect(session.activeChat.value?.title).toBe('Auth bug 2')
  })

  it('a new chat after an open one gets its own number', async () => {
    const { session, start } = await setup(withA())
    start.startNew()
    await start.send('x')
    expect(session.state.activeChatId).toBe(NEW_AFTER_A)
  })
})
