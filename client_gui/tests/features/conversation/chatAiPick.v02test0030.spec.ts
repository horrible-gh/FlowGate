import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationRequestError } from '@/features/conversation/api'
import { createChatStart } from '@/features/conversation/chatStart'
import { ConversationPanel } from '@/features/conversation'
import { createConversationSession } from '@/features/conversation/conversationSession'
import type { ChatDocSettings, ChatDocSettingsPatch, ConversationTurn } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

registerAppMessages(i18n)

// flowgate.v02test.0030 (0001-B) — the chat's AI never changes without the user: a new chat keeps the AI picked in its
// draft even when the screen moved on during the create; a reopened chat waits for its stored AI (a failed read says
// so and offers a retry instead of sending on my default AI); a chat with no stored AI opens on the AI that last
// answered in it; the same Flow coming back after a blink keeps the chat and its AI; every AI call says how its AI was
// chosen (startChatRun's 4th argument, kept on the run as client_provider_origin).
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'
const EMPTY: ChatDocSettings = { providerId: null, checkpointSeq: null, checkpointKind: null }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

async function setup(stored: Record<string, ChatDocSettings> = {}, turnsB: ConversationTurn[] = [turn(1, 'user', 'other chat', null)]) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Auth bug', updatedAt: '2026-11-17T03:00:00Z' },
      { id: B, title: 'Git conflict', updatedAt: '2026-11-16T03:00:00Z' },
    ],
    turns: { [A]: [turn(1, 'user', 'look', null)], [B]: turnsB },
    settings: { ...createFakeSource().data.settings, sendAction: 'invoke_ai' as const },
  })
  const server: Record<string, ChatDocSettings> = { ...stored }
  const source = fake.source
  source.fetchChatDocSettings = vi.fn(async (chatId: string) => ({ ...EMPTY, ...server[chatId] }))
  source.saveChatDocSettings = vi.fn(async (chatId: string, patch: ChatDocSettingsPatch) => {
    const current: ChatDocSettings = { ...EMPTY, ...server[chatId] }
    if ('provider_id' in patch) current.providerId = patch.provider_id ?? null
    server[chatId] = current
    return { ...current }
  })
  const session = createConversationSession({ source, sleep: async () => undefined })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, source, server, session }
}

describe('v02test.0030 a new chat keeps the AI picked in its draft', () => {
  it('is stored even when the screen leaves the draft while the chat is being made, and comes back with the chat', async () => {
    const { session, source, server } = await setup()
    const start = createChatStart({ session, source, defaultTitle: () => '새 대화' })
    start.startNew()
    session.selectProvider('sonnet')
    const gate = deferred<void>()
    const create = source.createChat.getMockImplementation()!
    source.createChat.mockImplementation(async (...args: unknown[]) => { await gate.promise; return create(...args) })

    const sent = start.send('hello')
    await flushPromises()
    await session.selectChat(A) // the screen moves on before the create answers
    await flushPromises()
    gate.resolve()
    await sent
    await flushPromises()

    const made = Object.keys(server).find((id) => id !== A && id !== B)!
    expect(made).toBeTruthy()
    expect(server[made].providerId).toBe('sonnet')
    // The first message's AI call ran on the picked AI, said to be the user's pick.
    expect(source.startChatRun).toHaveBeenCalledWith(made, 'sonnet', expect.any(String), 'user_pick')
    // The chat open meanwhile keeps its own AI; the made chat reopens on the picked one.
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.selectedProviderId).toBe('opus')
    await session.selectChat(made)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('sonnet')
  })

  it('uses the AI picked when send was pressed, not the selection when the create answers', async () => {
    const { session, source, server } = await setup({ [A]: { ...EMPTY, providerId: 'opus' } })
    const start = createChatStart({ session, source, defaultTitle: () => '새 대화' })
    start.startNew()
    session.selectProvider('sonnet')
    const gate = deferred<void>()
    const create = source.createChat.getMockImplementation()!
    source.createChat.mockImplementation(async (...args: unknown[]) => { await gate.promise; return create(...args) })
    const sent = start.send('hello')
    await flushPromises()
    await session.selectChat(A) // A shows opus now
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus')
    gate.resolve()
    await sent
    await flushPromises()
    const made = Object.keys(server).find((id) => id !== A && id !== B)!
    expect(server[made].providerId).toBe('sonnet')
    expect(server[A].providerId).toBe('opus')
  })
})

describe('v02test.0030 a reopened chat waits for its stored AI', () => {
  it('sending waits until the stored AI is read, then goes on that AI (origin stored)', async () => {
    const { session, source } = await setup({ [B]: { ...EMPTY, providerId: 'sonnet' } })
    const read = deferred<ChatDocSettings>()
    source.fetchChatDocSettings.mockImplementationOnce(() => read.promise)
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('loading')
    expect(await session.send('too early')).toBe(false)
    await session.invokeAi()
    expect(source.startChatRun).not.toHaveBeenCalled()

    read.resolve({ ...EMPTY, providerId: 'sonnet' })
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String), 'stored')
  })

  it('a pick this screen already has for the chat is usable at once', async () => {
    const { session, source } = await setup()
    await session.selectChat(B)
    await flushPromises()
    session.selectProvider('sonnet')
    await session.selectChat(A)
    await flushPromises()
    source.fetchChatDocSettings.mockImplementationOnce(() => new Promise(() => undefined)) // never answers
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String), 'user_pick')
  })

  it('a failed read keeps sending stopped with the reason (never my default AI), and a retry reads it again', async () => {
    const { session, source } = await setup({ [B]: { ...EMPTY, providerId: 'sonnet' } })
    source.fetchChatDocSettings.mockRejectedValueOnce(new Error('offline'))
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('error')
    expect(session.state.chatProviderError).toContain('offline')
    expect(await session.send('x')).toBe(false)
    expect(source.postTurn).not.toHaveBeenCalled()

    session.retryChatProvider()
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(await session.send('go')).toBe(true)
  })

  it('picking an AI by hand settles a chat whose read failed', async () => {
    const { session, source } = await setup()
    source.fetchChatDocSettings.mockRejectedValueOnce(new Error('offline'))
    await session.selectChat(B)
    await flushPromises()
    session.selectProvider('sonnet')
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String), 'user_pick')
  })

  it('a server without per-chat settings (404) is a chat with nothing stored, not a failure', async () => {
    const { session, source } = await setup()
    source.fetchChatDocSettings.mockRejectedValueOnce(new ConversationRequestError('no route', 404, 'not_found'))
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(session.state.selectedProviderId).toBe('opus')
  })

  it('the composer shows no line while the AI is checked, says when it could not be checked, and its retry reads it again', async () => {
    i18n.global.locale.value = 'ko'
    const { source } = await setup()
    const read = deferred<ChatDocSettings>()
    source.fetchChatDocSettings.mockImplementation(() => read.promise)
    const wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId: 'p.m.0001', source },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
    await wrapper.find('[data-test="conv-input"]').setValue('hello')
    expect(wrapper.find('[data-test="conv-ai-pick-loading"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('이 채팅의 AI를 확인하는 중입니다')
    expect(wrapper.find('[data-test="conv-send"]').attributes('disabled')).toBeDefined()

    read.reject(new Error('offline'))
    await flushPromises()
    const error = wrapper.find('[data-test="conv-ai-pick-error"]')
    expect(error.text()).toContain('이 채팅의 AI를 확인하지 못해 전송을 멈췄습니다')
    expect(error.text()).toContain('offline')
    expect(wrapper.find('[data-test="conv-send"]').attributes('disabled')).toBeDefined()

    source.fetchChatDocSettings.mockImplementation(async () => ({ ...EMPTY, providerId: 'sonnet' }))
    await wrapper.find('[data-test="conv-ai-pick-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="conv-ai-pick-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="conv-send"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})

describe('v02test.0030 a chat with no stored AI opens on the AI that last answered in it', () => {
  const answeredBy = (key: string, name: string) => [
    turn(1, 'user', 'q', null),
    turn(2, 'ai', 'a', null, { participantKey: key, displayName: name }),
    turn(3, 'user', 'more', null),
  ]

  it('takes that AI (not my default) and says so on the AI call', async () => {
    const { session, source } = await setup({}, answeredBy('provider:sonnet', 'Sonnet 5'))
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(session.state.providerOrigin).toBe('last_answered')
    expect(session.state.notice).toBeNull()
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String), 'last_answered')
  })

  it('falls back to my default only when that AI is gone, and says which AI it was', async () => {
    const { session } = await setup({}, answeredBy('provider:ghost', 'Ghost AI'))
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus')
    expect(session.state.providerOrigin).toBe('default_fallback')
    expect(session.state.notice).toMatchObject({ code: 'chat_ai_fallback', detail: 'Ghost AI' })
  })

  it('a stored AI that is gone falls back to my default with the same notice', async () => {
    const { session } = await setup({ [B]: { ...EMPTY, providerId: 'ghost' } })
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus')
    expect(session.state.notice).toMatchObject({ code: 'chat_ai_fallback', detail: 'ghost' })
  })

  it('a chat nobody answered yet opens on my default AI without a notice', async () => {
    const { session } = await setup()
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus')
    expect(session.state.notice).toBeNull()
  })
})

describe('v02test.0030 the Flow blinking away (scope null) and back', () => {
  it('reopens the chat that was open, on its AI — not the newest chat on my default AI', async () => {
    const { session, source } = await setup()
    await session.selectChat(B)
    await flushPromises()
    session.selectProvider('sonnet')
    await session.setScope(null)
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(session.state.chatProviderStatus).toBe('ready')
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String), 'user_pick')
  })

  it('another Flow in between opens the newest chat as before', async () => {
    const { session } = await setup()
    await session.selectChat(B)
    await flushPromises()
    await session.setScope(null)
    await session.setScope({ projectId: 'p', groupId: 'p.m.0002' })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.activeChatId).toBe(A)
  })
})
