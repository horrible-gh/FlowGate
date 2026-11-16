import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { createChatStart } from '@/features/conversation/chatStart'
import {
  createConversationSession,
  OUTBOX_TTL_MS,
  outboxStorageKey,
  readStoredSends,
  SEND_RETRY_MAX,
  type StoredSend,
} from '@/features/conversation/conversationSession'
import { draftUserId } from '@/features/conversation/drafts'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02gui.0028 — the first message of a new chat shows at once (보내는 중) from the press of send, through the
// chat's create and opening to the POST, as one message with one idempotency key; and every send the server has not
// confirmed yet is kept per user in localStorage so an F5 brings it back (sent again with the same key, or failed with
// its retry) — never for another user, never past its TTL, and never by creating a chat on reload.
registerAppMessages(i18n)

const scope = { projectId: 'p', groupId: 'p.m.0001' }
const CH = 'p.m.0001.0002-CH'
type Fake = ReturnType<typeof createFakeSource>

function deferred<T = void>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function signIn(user: string | null): void {
  const w = window as unknown as { __accessToken__?: string }
  if (user) w.__accessToken__ = `h.${btoa(JSON.stringify({ username: user }))}.s`
  else delete w.__accessToken__
}

function withChat(): Fake {
  return createFakeSource({ chats: [{ id: CH, title: 'A', updatedAt: '2026-11-13T04:00:00Z' }], turns: { [CH]: [turn(1, 'user', 'hi')] } })
}

async function setup(fake: Fake = createFakeSource()) {
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => 'new' })
  await session.setScope(scope)
  await flushPromises()
  return { ...fake, fake, session, start }
}

const stored = (user = draftUserId()) => readStoredSends(user, 'p')
const emptyPage = { turns: [], headSeq: 0, participants: [], myReadSeq: null, nextAfterSeq: null, prevBeforeSeq: null }

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  signIn('alice')
})
afterEach(() => signIn(null))

describe('0028 the first message shows from the press of send', () => {
  it('is a 보내는 중 bubble through create and load, handed to the created chat with the same key', async () => {
    const x = await setup()
    const create = deferred<{ id: string; title: string; updatedAt: null }>()
    x.source.createChat.mockImplementationOnce(() => create.promise)
    const sending = x.start.send('첫 메시지')
    // Synchronously, before the create answers.
    expect(x.start.phase.value?.phase).toBe('creating')
    expect(x.session.state.pending).toMatchObject([{ body: '첫 메시지', failed: false }])
    expect(x.session.timeline.value).toMatchObject([{ kind: 'message', pending: true, failed: false }])
    const key = x.session.state.pending[0].idempotencyKey
    expect(stored()).toMatchObject([{ chatId: null, phase: 'creating', idempotencyKey: key, body: '첫 메시지', groupId: scope.groupId }])

    const history = deferred<typeof emptyPage>()
    x.source.fetchTurns.mockImplementationOnce(() => history.promise)
    create.resolve({ id: 'p.m.0001.0010-CH', title: 'new', updatedAt: null })
    await flushPromises()
    expect(x.start.phase.value?.phase).toBe('loading')
    expect(x.session.state.activeChatId).toBe('p.m.0001.0010-CH')
    expect(x.session.state.turnsStatus).toBe('loading')
    expect(x.session.state.pending).toMatchObject([{ idempotencyKey: key, failed: false }])
    expect(stored()).toMatchObject([{ chatId: 'p.m.0001.0010-CH', phase: 'loading', idempotencyKey: key }])
    expect(x.source.postTurn).not.toHaveBeenCalled()

    history.resolve(emptyPage)
    expect(await sending).toBe(true)
    expect(x.source.postTurn).toHaveBeenCalledTimes(1)
    expect(x.source.postTurn).toHaveBeenCalledWith('p.m.0001.0010-CH', expect.objectContaining({ body: '첫 메시지', idempotencyKey: key }))
    expect(x.session.state.pending).toEqual([])
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['첫 메시지'])
    expect(stored()).toEqual([])
    expect(localStorage.getItem(outboxStorageKey(draftUserId(), 'p'))).toBeNull()
    x.session.dispose()
  })

  it('a failed create leaves the bubble failed; its retry asks for the same chat and sends the same key', async () => {
    const x = await setup()
    x.source.createChat.mockRejectedValueOnce(httpError(503, 'down'))
    expect(await x.start.send('다시')).toBe(false)
    expect(x.start.phase.value).toMatchObject({ phase: 'failed', stage: 'create' })
    const [bubble] = x.session.state.pending
    expect(bubble).toMatchObject({ body: '다시', failed: true })
    expect(x.session.timeline.value).toMatchObject([{ kind: 'message', pending: false, failed: true, localId: bubble.localId }])
    expect(stored()).toMatchObject([{ phase: 'failed', chatId: null, idempotencyKey: bubble.idempotencyKey }])
    const intent = x.source.createChat.mock.calls[0][2].clientRequestId
    expect(await x.start.retry(bubble.localId)).toBe(true)
    expect(x.source.createChat.mock.calls[1][2].clientRequestId).toBe(intent)
    expect(x.source.postTurn).toHaveBeenCalledTimes(1)
    expect(x.source.postTurn.mock.calls[0][1].idempotencyKey).toBe(bubble.idempotencyKey)
    expect(x.session.state.pending).toEqual([])
    expect(stored()).toEqual([])
    x.session.dispose()
  })

  it('a composer resubmission of the same text after a failed create is the same message (one bubble, one key)', async () => {
    const x = await setup()
    x.source.createChat.mockRejectedValueOnce(httpError(503, 'down'))
    expect(await x.start.send('같은 글')).toBe(false)
    const [bubble] = x.session.state.pending
    expect(await x.start.send('같은 글')).toBe(true)
    expect(x.source.postTurn.mock.calls.map((c: any[]) => c[1].idempotencyKey)).toEqual([bubble.idempotencyKey])
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['같은 글'])
    x.session.dispose()
  })

  it('a refused create (403) keeps the failed bubble on screen but not in storage', async () => {
    const x = await setup()
    x.source.createChat.mockRejectedValueOnce(httpError(403, 'forbidden'))
    expect(await x.start.send('권한 없음')).toBe(false)
    expect(x.session.state.pending).toMatchObject([{ body: '권한 없음', failed: true }])
    expect(stored()).toEqual([])
    x.session.dispose()
  })

  it('a failed history load leaves the bubble failed in the new chat, nothing posted; after the reload it is sent with its key', async () => {
    const x = await setup()
    x.source.fetchTurns.mockRejectedValueOnce(httpError(503, 'history'))
    expect(await x.start.send('로드 실패')).toBe(false)
    expect(x.start.phase.value).toMatchObject({ phase: 'failed', stage: 'load' })
    expect(x.session.state.turnsStatus).toBe('error')
    const [bubble] = x.session.state.pending
    expect(bubble).toMatchObject({ body: '로드 실패', failed: true })
    expect(x.source.postTurn).not.toHaveBeenCalled()
    const chatId = x.session.state.activeChatId!
    expect(stored()).toMatchObject([{ chatId, phase: 'failed', idempotencyKey: bubble.idempotencyKey }])
    await x.session.selectChat(chatId)
    expect(x.session.state.turnsStatus).toBe('ready')
    expect(x.session.state.pending).toMatchObject([{ localId: bubble.localId, failed: true }])
    expect(await x.start.retry(bubble.localId)).toBe(true)
    expect(x.source.postTurn.mock.calls.map((c: any[]) => c[1].idempotencyKey)).toEqual([bubble.idempotencyKey])
    expect(stored()).toEqual([])
    x.session.dispose()
  })

  it('sending goes on off screen after a Flow switch during the create, with the key of the bubble', async () => {
    const x = await setup()
    const create = deferred<{ id: string; title: string; updatedAt: null }>()
    x.source.createChat.mockImplementationOnce(() => create.promise)
    const sending = x.start.send('이동')
    const key = x.session.state.pending[0].idempotencyKey
    await x.session.setScope({ projectId: 'p', groupId: 'p.m.0002' })
    expect(x.session.state.pending).toEqual([])
    create.resolve({ id: 'p.m.0001.0010-CH', title: 'new', updatedAt: null })
    expect(await sending).toBe(true)
    expect(x.source.postTurn).toHaveBeenCalledWith('p.m.0001.0010-CH', expect.objectContaining({ idempotencyKey: key, basedOnSeq: 0 }))
    expect(stored()).toEqual([])
    x.session.dispose()
  })
})

describe('0028 ConversationPanel: the bubble is on screen during create and load', () => {
  let wrapper: VueWrapper | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('shows the message with 보내는 중 from the press until the server stored it', async () => {
    i18n.global.locale.value = 'ko'
    const fake = createFakeSource()
    const create = deferred<{ id: string; title: string; updatedAt: null }>()
    const history = deferred<typeof emptyPage>()
    fake.source.createChat.mockImplementationOnce(() => create.promise)
    wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: scope.groupId, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
    const bubbles = () => wrapper!.findAll('[data-test="tl-message"]')
    const sendingLabel = i18n.global.t('gui.conversation.message.sending')
    await find('conv-input').setValue('패널 첫 메시지')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect(bubbles()).toHaveLength(1)
    expect(bubbles()[0].find('.tl-text').text()).toBe('패널 첫 메시지')
    expect(bubbles()[0].classes()).toContain('is-pending')
    expect(bubbles()[0].find('.tl-time').text()).toBe(sendingLabel)

    fake.source.fetchTurns.mockImplementationOnce(() => history.promise)
    create.resolve({ id: 'p.m.0001.0010-CH', title: 'new', updatedAt: null })
    await flushPromises()
    expect(find('conv-log-loading').exists()).toBe(true)
    expect(bubbles()).toHaveLength(1)
    expect(bubbles()[0].find('.tl-time').text()).toBe(sendingLabel)

    history.resolve(emptyPage)
    await flushPromises()
    expect(bubbles()).toHaveLength(1)
    expect(bubbles()[0].classes()).not.toContain('is-pending')
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
  })
})

describe('0028 unconfirmed sends survive F5', () => {
  it('a send still on its way comes back 보내는 중 in a fresh session and is sent again with the same key', async () => {
    const fake = withChat()
    const first = await setup(fake)
    fake.source.postTurn.mockImplementationOnce(() => new Promise(() => {}))
    void first.start.send('끊긴 메시지')
    await flushPromises()
    const [item] = first.session.state.pending
    expect(stored()).toMatchObject([{ chatId: CH, phase: 'sending', idempotencyKey: item.idempotencyKey, basedOnSeq: 1, body: '끊긴 메시지' }])
    first.session.dispose()

    const gate = deferred()
    const post = fake.source.postTurn.getMockImplementation()!
    fake.source.postTurn.mockImplementationOnce(async (chatId: string, input: any) => {
      await gate.promise
      return post(chatId, input)
    })
    const second = await setup(fake)
    expect(second.session.state.activeChatId).toBe(CH)
    expect(second.session.state.pending).toMatchObject([{ localId: item.localId, idempotencyKey: item.idempotencyKey, failed: false }])
    expect(second.session.timeline.value.at(-1)).toMatchObject({ kind: 'message', pending: true })
    expect(fake.source.postTurn).toHaveBeenCalledTimes(2)
    expect(fake.source.postTurn.mock.calls[1]).toEqual([CH, expect.objectContaining({ idempotencyKey: item.idempotencyKey, body: '끊긴 메시지', basedOnSeq: 1 })])
    gate.resolve()
    await flushPromises()
    expect(second.session.state.pending).toEqual([])
    expect(second.session.state.turns.map((t) => t.body)).toContain('끊긴 메시지')
    expect(stored()).toEqual([])
    second.session.dispose()
  })

  it('a send that had failed (retries used up) comes back failed, is not sent by the reload, and retries with its key', async () => {
    const fake = withChat()
    const first = await setup(fake)
    for (let i = 0; i <= SEND_RETRY_MAX; i++) fake.source.postTurn.mockRejectedValueOnce(httpError(503, 'down'))
    expect(await first.start.send('실패')).toBe(false)
    const [item] = first.session.state.pending
    expect(stored()).toMatchObject([{ chatId: CH, phase: 'failed', idempotencyKey: item.idempotencyKey }])
    first.session.dispose()
    const posts = fake.source.postTurn.mock.calls.length

    const second = await setup(fake)
    expect(fake.source.postTurn).toHaveBeenCalledTimes(posts)
    expect(second.session.state.pending).toMatchObject([{ localId: item.localId, failed: true }])
    expect(await second.start.retry(item.localId)).toBe(true)
    expect(fake.source.postTurn.mock.calls.at(-1)![1].idempotencyKey).toBe(item.idempotencyKey)
    expect(stored()).toEqual([])
    second.session.dispose()
  })

  it('a refusal for good (422) is no longer kept: the bubble stays with its retry, F5 does not bring it back', async () => {
    const fake = withChat()
    const first = await setup(fake)
    fake.source.postTurn.mockRejectedValueOnce(httpError(422, 'invalid'))
    expect(await first.start.send('거부')).toBe(false)
    expect(first.session.state.pending).toMatchObject([{ body: '거부', failed: true }])
    expect(stored()).toEqual([])
    first.session.dispose()
    const second = await setup(fake)
    expect(second.session.state.pending).toEqual([])
    second.session.dispose()
  })

  it('a first message whose chat was never confirmed made comes back failed in its draft; no chat is created by the reload', async () => {
    const fake = withChat()
    const first = await setup(fake)
    first.start.startNew()
    fake.source.createChat.mockImplementationOnce(() => new Promise(() => {}))
    void first.start.send('생성 중 F5')
    await flushPromises()
    const [item] = first.session.state.pending
    const intent = fake.source.createChat.mock.calls[0][2].clientRequestId
    expect(stored()).toMatchObject([{ chatId: null, phase: 'creating' }])
    first.session.dispose()

    const second = await setup(fake)
    expect(fake.source.createChat).toHaveBeenCalledTimes(1)
    // The draft stays open on the message instead of the newest chat.
    expect(second.start.isDraft.value).toBe(true)
    expect(second.session.state.activeChatId).toBeNull()
    expect(second.session.state.pending).toMatchObject([{ localId: item.localId, body: '생성 중 F5', failed: true }])
    expect(stored()).toMatchObject([{ chatId: null, phase: 'failed' }])
    expect(fake.source.postTurn).not.toHaveBeenCalled()

    expect(await second.start.retry(item.localId)).toBe(true)
    expect(fake.source.createChat.mock.calls[1][2].clientRequestId).toBe(intent)
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
    expect(fake.source.postTurn.mock.calls[0][1].idempotencyKey).toBe(item.idempotencyKey)
    expect(stored()).toEqual([])
    second.session.dispose()
  })

  it('a first message whose chat was made before F5 is sent to that chat again with its key', async () => {
    const fake = createFakeSource()
    const first = await setup(fake)
    fake.source.fetchTurns.mockImplementationOnce(() => new Promise(() => {}))
    void first.start.send('로딩 중 F5')
    await flushPromises()
    const [item] = first.session.state.pending
    const chatId = first.session.state.activeChatId!
    expect(stored()).toMatchObject([{ chatId, phase: 'loading' }])
    first.session.dispose()

    const second = await setup(fake)
    await flushPromises()
    expect(fake.source.createChat).toHaveBeenCalledTimes(1)
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
    expect(fake.source.postTurn.mock.calls[0]).toEqual([chatId, expect.objectContaining({ idempotencyKey: item.idempotencyKey })])
    expect(second.session.state.activeChatId).toBe(chatId)
    expect(second.session.state.turns.map((t) => t.body)).toEqual(['로딩 중 F5'])
    expect(second.session.state.pending).toEqual([])
    expect(stored()).toEqual([])
    second.session.dispose()
  })

  it('another user never sees or sends them; their owner does after signing in again', async () => {
    const fake = withChat()
    const first = await setup(fake)
    fake.source.postTurn.mockImplementationOnce(() => new Promise(() => {}))
    void first.start.send('alice 메시지')
    await flushPromises()
    const [item] = first.session.state.pending
    first.session.dispose()

    signIn('bob')
    const bob = await setup(fake)
    expect(bob.session.state.pending).toEqual([])
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
    expect(stored('bob')).toEqual([])
    expect(stored('alice')).toHaveLength(1)
    bob.session.dispose()

    signIn('alice')
    const again = await setup(fake)
    await flushPromises()
    expect(fake.source.postTurn).toHaveBeenCalledTimes(2)
    expect(fake.source.postTurn.mock.calls[1][1].idempotencyKey).toBe(item.idempotencyKey)
    expect(stored('alice')).toEqual([])
    again.session.dispose()
  })

  it('a sign-out during the send leaves it kept for its user instead of marking it failed there', async () => {
    const fake = withChat()
    const x = await setup(fake)
    const post = deferred()
    fake.source.postTurn.mockImplementationOnce(async () => {
      await post.promise
      throw httpError(503, 'down')
    })
    const sending = x.start.send('로그아웃')
    await flushPromises()
    signIn('bob')
    post.resolve()
    expect(await sending).toBe(false)
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
    expect(stored('alice')).toMatchObject([{ phase: 'sending', body: '로그아웃' }])
    x.session.dispose()
  })

  it('entries past the TTL are dropped (every user) and never sent', async () => {
    const old = new Date(Date.now() - OUTBOX_TTL_MS - 60_000).toISOString()
    const entry = (user: string, localId: string): StoredSend => ({
      localId, idempotencyKey: localId, body: `${user} 오래됨`, basedOnSeq: 1, createdAt: old, projectId: 'p', groupId: scope.groupId,
      chatId: CH, intentId: null, createIntentId: null, phase: 'sending', providerId: 'opus',
    })
    localStorage.setItem(outboxStorageKey('alice', 'p'), JSON.stringify([entry('alice', 'sess_old_alice')]))
    localStorage.setItem(outboxStorageKey('carol', 'p'), JSON.stringify([entry('carol', 'sess_old_carol')]))
    const x = await setup(withChat())
    await flushPromises()
    expect(x.source.postTurn).not.toHaveBeenCalled()
    expect(x.session.state.pending).toEqual([])
    expect(localStorage.getItem(outboxStorageKey('alice', 'p'))).toBeNull()
    expect(localStorage.getItem(outboxStorageKey('carol', 'p'))).toBeNull()
    x.session.dispose()
  })
})
