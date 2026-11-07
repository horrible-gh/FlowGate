import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { ConversationRequestError } from '@/features/conversation/api'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, httpError } from './fakeConversationSource'

const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
async function setup(mode: 'empty' | 'error' | 'unavailable') {
  const fake = createFakeSource()
  if (mode === 'empty') fake.source.listProviders.mockResolvedValueOnce({ providers: [], defaultProviderId: null })
  if (mode === 'error') fake.source.listProviders.mockRejectedValueOnce(httpError(503, 'isolated list failure'))
  let modelCalls = 0
  const original = fake.source.autoTitleChat.getMockImplementation()!
  fake.source.autoTitleChat.mockImplementation(async (...args: unknown[]) => { modelCalls++; return original(...args) })
  if (mode === 'unavailable') fake.source.autoTitleChat.mockRejectedValueOnce(httpError(409, 'provider_unavailable', 'conversation_auto_title_unavailable'))
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => 'New chat' })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session, start, modelCalls: () => modelCalls }
}

describe('0147 owed recovery before/after', () => {
  it.each(['empty', 'error'] as const)('%s providers retain the obligation until readiness recovers', async (mode) => {
    const { source, session, start, modelCalls } = await setup(mode)
    expect(await start.send('isolated title recovery')).toBe(true)
    await flushPromises()
    const id = session.state.activeChatId!
    expect(source.autoTitleChat).toHaveBeenCalledTimes(0)
    expect(modelCalls()).toBe(0)
    source.listChats.mockResolvedValueOnce([])
    await session.setScope({ projectId: 'other', groupId: 'other.m.0001' })
    await session.setScope(SCOPE)
    await session.selectChat(id)
    await start.send('accepted recovery trigger')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(modelCalls()).toBe(1)
    expect(session.activeChat.value?.title).toBe('AI: isolated title recovery')
    await start.send('no duplicate title')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    session.dispose()
  })

  it('unavailable preserves owed for the next accepted send without immediate retry', async () => {
    const { source, session, start, modelCalls } = await setup('unavailable')
    await start.send('isolated unavailable recovery')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(modelCalls()).toBe(0)
    await start.send('accepted recovery trigger')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(2)
    expect(modelCalls()).toBe(1)
    expect(session.activeChat.value?.title).toBe('AI: isolated unavailable recovery')
    session.dispose()
  })
})

const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
const marker = (state: 'owed' | 'claimed' | 'done' | 'manual' | 'failed') => ({ state, provisionalTitle: 'New chat' })
async function restored(state: 'owed' | 'claimed' | 'done' | 'manual' | 'failed' | undefined = 'owed', hasUser = true) {
  const fake = createFakeSource({ chats: [{ id: A, title: 'New chat', updatedAt: null, ...(state ? { autoTitle: marker(state) } : {}) }],
    turns: { [A]: hasUser ? [{ seq: 1, speaker: 'user', body: 'stored synthetic input', sourceRunId: null, createdAt: null }] : [] } })
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => 'New chat' })
  return { ...fake, session, start, open: async () => { await session.setScope(SCOPE); await flushPromises() } }
}

describe('0147 persisted obligation, bounded recovery and attribution', () => {
  it('remount recovers only server owed with a saved user turn and no send/start', async () => {
    const x = await restored()
    await x.open()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.session.activeChat.value).toMatchObject({ title: 'AI: stored synthetic input', autoTitle: marker('done') })
    x.session.dispose()
    const fresh = createConversationSession({ source: x.source, sleep: async () => undefined })
    createChatStart({ session: fresh, source: x.source, defaultTitle: () => 'New chat' })
    await fresh.setScope(SCOPE); await flushPromises()
    expect(fresh.activeChat.value?.title).toBe('AI: stored synthetic input')
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.source.postTurn).not.toHaveBeenCalled()
    expect(x.source.startChatRun).not.toHaveBeenCalled()
    fresh.dispose()
  })

  it.each(['claimed', 'done', 'manual', 'failed', undefined] as const)('does not infer owed for %s at reentry', async (state) => {
    const x = await restored(state)
    if (state === undefined) x.data.chats[0].autoTitle = undefined
    await x.open(); await x.session.reloadChats(); await x.start.send('another synthetic input'); await flushPromises()
    expect(x.source.autoTitleChat).not.toHaveBeenCalled()
    x.session.dispose()
  })

  it('has no request/model call for owed without a valid user turn', async () => {
    const x = await restored('owed', false)
    await x.open(); await x.session.reloadChats(); await flushPromises()
    expect(x.source.autoTitleChat).not.toHaveBeenCalled()
    await x.start.send('first accepted input'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it('keeps the obligation when a source does not support autoTitleChat', async () => {
    const x = await restored()
    const title = x.source.autoTitleChat
    x.source.autoTitleChat = undefined
    await x.open()
    expect(x.session.activeChat.value?.autoTitle?.state).toBe('owed')
    x.source.autoTitleChat = title
    await x.start.send('next acceptance'); await flushPromises()
    expect(title).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it.each(['done', 'claimed', 'owed', 'manual', 'failed'] as const)('reconciles a lost response to %s with at most one read', async (state) => {
    const x = await restored()
    x.source.autoTitleChat.mockImplementationOnce(async () => {
      x.data.chats[0].autoTitle = marker(state)
      if (state === 'done' || state === 'manual') x.data.chats[0].title = `stored ${state}`
      throw new ConversationRequestError('', null, null)
    })
    await x.open()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.source.listChats).toHaveBeenCalledTimes(2)
    for (let n = 0; n < 3; n++) await x.session.reloadChats()
    await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    await x.start.send('next acceptance'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(state === 'owed' ? 2 : 1)
    if (state === 'done' || state === 'manual') expect(x.session.activeChat.value?.title).toBe(`stored ${state}`)
    x.session.dispose()
  })

  it('leaves ambiguous request plus failed list unresolved without a request loop', async () => {
    const x = await restored()
    x.source.autoTitleChat.mockImplementationOnce(async () => {
      x.source.listChats.mockRejectedValueOnce(httpError(503, 'list unavailable'))
      throw new ConversationRequestError('', null, null)
    })
    await x.open(); await flushPromises(); await x.start.send('no blind retry'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.source.listChats).toHaveBeenCalledTimes(2)
    expect(x.session.activeChat.value?.autoTitle?.state).toBe('owed')
    await x.session.reloadChats(); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(2)
    x.session.dispose()
  })

  it.each(['claimed', 'done', 'manual', 'failed'] as const)('not_owed is read as %s, never assumed successful', async (state) => {
    const x = await restored()
    x.source.autoTitleChat.mockImplementationOnce(async () => {
      x.data.chats[0].autoTitle = marker(state)
      if (state === 'done') x.data.chats[0].title = 'another client title'
      throw httpError(409, 'not owed', 'conversation_auto_title_not_owed')
    })
    await x.open(); await x.start.send('another accepted turn'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.session.activeChat.value?.autoTitle?.state).toBe(state)
    if (state === 'done') expect(x.session.activeChat.value?.title).toBe('another client title')
    x.session.dispose()
  })

  it.each(['conversation_auto_title_failed', 'conversation_title_changed', 'conversation_group_disposed'])("%s is terminal across send/provider changes", async (code) => {
    const x = await restored()
    x.source.autoTitleChat.mockRejectedValueOnce(httpError(code.includes('failed') ? 502 : 409, code, code))
    await x.open(); await x.start.send('later'); x.session.selectProvider('sonnet'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it.each([{ id: '', title: 'wrong' }, { id: B, title: 'wrong' }, { id: A, title: '  ' }])('refuses malformed success %j', async (saved) => {
    const x = await restored()
    x.source.autoTitleChat.mockResolvedValueOnce({ ...saved, updatedAt: null })
    await x.open()
    expect(x.session.activeChat.value?.title).toBe('New chat')
    expect(x.session.activeChat.value?.autoTitle?.state).toBe('owed')
    expect(x.source.listChats).toHaveBeenCalledTimes(2)
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it('manual rename back to provisional wins over a delayed successful title response', async () => {
    const x = await restored()
    let answer!: (value: unknown) => void
    x.source.autoTitleChat.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    await x.open()
    await x.start.rename('manual title'); await x.start.rename('New chat')
    answer({ id: A, title: 'late automatic title', updatedAt: null }); await flushPromises()
    expect(x.session.activeChat.value).toMatchObject({ title: 'New chat', autoTitle: marker('manual') })
    await x.start.send('later'); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it('an older list cannot roll back done or manual after an accepted title write', async () => {
    const x = await restored('done')
    await x.open()
    let answer!: (value: unknown) => void
    const stale = structuredClone(x.data.chats)
    x.source.listChats.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    const pending = x.session.reloadChats()
    await x.start.rename('manual winner')
    answer(stale); await pending
    expect(x.session.activeChat.value).toMatchObject({ title: 'manual winner', autoTitle: marker('manual') })
    x.session.dispose()
  })

  it('multiple chats receive reversed title answers by id; draft and active header are independent', async () => {
    const fake = createFakeSource()
    const answers = new Map<string, (value: unknown) => void>()
    fake.source.autoTitleChat.mockImplementation((id: string) => new Promise((resolve) => { answers.set(id, resolve) }))
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    const start = createChatStart({ session, source: fake.source, defaultTitle: () => 'New chat' })
    await session.setScope(SCOPE)
    await start.send('first'); await flushPromises(); const first = session.state.activeChatId!
    start.startNew(); await start.send('second'); await flushPromises(); const second = session.state.activeChatId!
    answers.get(second)!({ id: second, title: 'Second saved title' }); await flushPromises()
    answers.get(first)!({ id: first, title: 'First saved title' }); await flushPromises()
    expect(session.activeChat.value?.title).toBe('Second saved title')
    expect(session.state.chats.find((chat) => chat.id === first)?.title).toBe('First saved title')
    expect(fake.source.autoTitleChat).toHaveBeenCalledTimes(2)
    session.dispose()
  })

  it('in-flight lock spans provider wait, repeated sends and reloads', async () => {
    const x = await restored()
    let answer!: (value: unknown) => void
    x.source.autoTitleChat.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    await x.open(); await x.start.send('second'); x.session.selectProvider('sonnet'); await x.session.reloadChats(); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    answer({ id: A, title: 'one title' }); await flushPromises()
    expect(x.session.activeChat.value?.title).toBe('one title')
    x.session.dispose()
  })

  it('dispose rejects late results and stops title watchers', async () => {
    const x = await restored()
    let answer!: (value: unknown) => void
    x.source.autoTitleChat.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    await x.open(); x.session.dispose()
    answer({ id: A, title: 'late title' }); await flushPromises()
    expect(x.session.activeChat.value?.title).toBe('New chat')
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('an old provider wait cannot lock out a ready reentered project', async () => {
    const x = await restored()
    let release!: (value: unknown) => void
    x.source.listProviders.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    await x.open()
    x.source.listChats.mockResolvedValueOnce([])
    await x.session.setScope({ projectId: 'other', groupId: 'other.m.0001' })
    await x.session.setScope(SCOPE); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(x.source.autoTitleChat).toHaveBeenCalledWith(A, 'opus')
    release({ providers: [{ id: 'old', name: 'old' }], defaultProviderId: 'old' }); await flushPromises()
    expect(x.session.state.selectedProviderId).toBe('opus')
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })

  it('scope reentry during title HTTP restores the eventual server title without another call', async () => {
    const x = await restored()
    let finish!: () => void
    const original = x.source.autoTitleChat.getMockImplementation()!
    x.source.autoTitleChat.mockImplementationOnce((...args: unknown[]) => new Promise((resolve) => { finish = () => resolve(original(...args)) }))
    await x.open()
    x.source.listChats.mockResolvedValueOnce([])
    await x.session.setScope({ projectId: 'other', groupId: 'other.m.0001' })
    await x.session.setScope(SCOPE); await flushPromises()
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    finish(); await flushPromises()
    expect(x.session.activeChat.value?.title).toBe('AI: stored synthetic input')
    expect(x.source.autoTitleChat).toHaveBeenCalledTimes(1)
    x.session.dispose()
  })
})
