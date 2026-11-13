import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { ConversationRequestError } from '@/features/conversation/api'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession, SEND_RETRY_MAX } from '@/features/conversation/conversationSession'
import type { ChatSummary, ConversationTurn } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02gui.0009 — "채팅 전송 직후 다른 플로우로 이동하면 메시지 유실". A message whose send started belongs
// to the Flow and chat it was written in: moving to another Flow (or chat, or draft) right after pressing send neither
// drops it nor sends it anywhere else. A first message of a new chat goes to the chat its create made even when the
// screen moved on during the create or while that chat opened; a send failure off screen is kept for its chat (failed
// bubble with retry, recorded chat error) and its retry reuses the idempotency key, so the server stores one turn.
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const FLOW_A = { projectId: 'p', groupId: 'p.m.0001' }
const FLOW_B = { projectId: 'p', groupId: 'p.m.0002' }
const A1 = 'p.m.0001.0002-CH'
const B1 = 'p.m.0002.0002-CH'
const B2 = 'p.m.0002.0003-CH'
const B_TURNS = ['B 대화', '네']

function deferred<T = void>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}

/** One step of the fake server's POST /turn: wait for `gate`, then answer ok, refuse with a status, or store and lose the answer. */
interface PostStep { gate?: Promise<unknown>; outcome?: 'ok' | 'lost' | number }

/**
 * A fake server with one chat list per Flow whose turn store is keyed like the real one: a POST repeating a stored
 * (chat, idempotency key) answers the stored turn instead of adding one.
 */
function server(opts: { aChats?: boolean } = {}) {
  const fake = createFakeSource({
    turns: { [A1]: [turn(1, 'user', 'A 대화')], [B1]: [turn(1, 'user', B_TURNS[0]), turn(2, 'ai', B_TURNS[1])], [B2]: [turn(1, 'user', 'B 다른 채팅')] },
  })
  const chatsOf: Record<string, ChatSummary[]> = {
    [FLOW_A.groupId]: opts.aChats ? [{ id: A1, title: 'A 대화', updatedAt: '2026-11-13T04:00:00Z' }] : [],
    [FLOW_B.groupId]: [
      { id: B1, title: 'B 대화', updatedAt: '2026-11-13T04:00:00Z' },
      { id: B2, title: 'B 다른 채팅', updatedAt: '2026-11-13T03:00:00Z' },
    ],
  }
  let made = 0
  fake.source.listChats.mockImplementation(async (scope: { groupId: string }) => (chatsOf[scope.groupId] ?? []).map((c) => ({ ...c })))
  const creates: Array<Promise<unknown>> = []
  fake.source.createChat.mockImplementation(async (scope: { groupId: string }, title: string) => {
    const gate = creates.shift()
    if (gate) await gate
    made += 1
    const chat = { id: `${scope.groupId}.00${10 + made}-CH`, title, updatedAt: '2026-11-13T05:00:00Z' }
    chatsOf[scope.groupId].unshift(chat)
    fake.data.turns[chat.id] = []
    return { ...chat }
  })
  const steps: PostStep[] = []
  const stored = new Map<string, ConversationTurn>()
  let seq = 100
  fake.source.postTurn.mockImplementation(async (chatId: string, input: { body: string; idempotencyKey: string }) => {
    const step = steps.shift() ?? {}
    if (step.gate) await step.gate
    if (typeof step.outcome === 'number') throw httpError(step.outcome, `server ${step.outcome}`)
    const key = `${chatId}\n${input.idempotencyKey}`
    let saved = stored.get(key)
    if (!saved) {
      saved = turn(++seq, 'user', input.body, '2026-11-13T06:00:00Z')
      ;(fake.data.turns[chatId] ??= []).push(saved)
      stored.set(key, saved)
    }
    if (step.outcome === 'lost') throw new ConversationRequestError('timeout of 30000ms exceeded', null, null)
    return { turn: { ...saved }, headSeq: seq }
  })
  /** Every (chat, key) the client posted, in order. */
  const posts = () => fake.source.postTurn.mock.calls.map(([chatId, input]: [string, { idempotencyKey: string; body: string }]) =>
    ({ chatId, key: input.idempotencyKey, body: input.body }))
  const bodies = (chatId: string) => (fake.data.turns[chatId] ?? []).map((t) => t.body)
  return { ...fake, chatsOf, steps, creates, posts, bodies }
}

async function setup(opts: { aChats?: boolean } = {}) {
  const fake = server(opts)
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
  await session.setScope(FLOW_A)
  await flushPromises()
  return { ...fake, session, start }
}

type Setup = Awaited<ReturnType<typeof setup>>

/** Flow B is shown exactly as before: its own chat open, its turns and list untouched, nothing posted to it. */
function expectFlowBUntouched(x: Setup) {
  expect(x.session.state.scope).toEqual(FLOW_B)
  expect(x.session.state.activeChatId).toBe(B1)
  expect(x.session.state.turns.map((t) => t.body)).toEqual(B_TURNS)
  expect(x.session.state.pending).toEqual([])
  expect(x.chatsOf[FLOW_B.groupId].map((c) => c.id)).toEqual([B1, B2])
  expect(x.bodies(B1)).toEqual(B_TURNS)
  expect(x.posts().every((p) => p.chatId.startsWith(`${FLOW_A.groupId}.`))).toBe(true)
}

const chatErrors = (x: Setup) => x.session.state.activities.filter((a) => a.kind === 'status' && a.code === 'send_failed')

describe('an open chat: the POST has started when the Flow changes', () => {
  it('success: stored once in A, B untouched, back in A it shows', async () => {
    const x = await setup({ aChats: true })
    expect(x.session.state.activeChatId).toBe(A1)
    const answer = deferred()
    x.steps.push({ gate: answer.promise })
    const sending = x.start.send('보낸 직후 이동')
    expect(x.posts()).toEqual([{ chatId: A1, key: expect.any(String), body: '보낸 직후 이동' }]) // started before any switch
    await x.session.setScope(FLOW_B)
    await flushPromises()
    answer.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    expectFlowBUntouched(x)
    expect(x.bodies(A1)).toEqual(['A 대화', '보낸 직후 이동'])

    await x.session.setScope(FLOW_A)
    await flushPromises()
    expect(x.session.state.activeChatId).toBe(A1)
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['A 대화', '보낸 직후 이동'])
    expect(x.session.state.pending).toEqual([])
    expect(x.posts()).toHaveLength(1)
  })

  it('a transient failure off screen is replayed there with the same key and ends stored once', async () => {
    const x = await setup({ aChats: true })
    const answer = deferred()
    x.steps.push({ gate: answer.promise, outcome: 503 })
    const sending = x.start.send('재시도 필요')
    await x.session.setScope(FLOW_B)
    await flushPromises()
    answer.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    expectFlowBUntouched(x)
    const posts = x.posts()
    expect(posts).toHaveLength(2)
    expect(posts.every((p) => p.chatId === A1 && p.key === posts[0].key)).toBe(true)
    expect(x.bodies(A1)).toEqual(['A 대화', '재시도 필요'])
    expect(x.source.recordChatError).not.toHaveBeenCalled()
  })

  it('a stored turn whose answer was lost is not stored twice by its replay', async () => {
    const x = await setup({ aChats: true })
    const answer = deferred()
    x.steps.push({ gate: answer.promise, outcome: 'lost' })
    const sending = x.start.send('응답 유실')
    await x.session.setScope(FLOW_B)
    answer.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    expect(new Set(x.posts().map((p) => p.key)).size).toBe(1)
    expect(x.bodies(A1)).toEqual(['A 대화', '응답 유실'])
  })

  it('failure: kept for A (failed bubble, recorded error), shown on return; the retry reuses the key; one server turn', async () => {
    const x = await setup({ aChats: true })
    const answer = deferred()
    x.steps.push({ gate: answer.promise, outcome: 503 })
    for (let i = 0; i < SEND_RETRY_MAX; i++) x.steps.push({ outcome: 503 })
    const sending = x.start.send('실패할 메시지')
    await x.session.setScope(FLOW_B)
    await flushPromises()
    answer.resolve()
    expect(await sending).toBe(false)
    await flushPromises()
    // Nothing of it on B; the server was really asked (every replay) and really refused.
    expectFlowBUntouched(x)
    expect(chatErrors(x)).toEqual([])
    const posts = x.posts()
    expect(posts).toHaveLength(SEND_RETRY_MAX + 1)
    expect(posts.every((p) => p.chatId === A1 && p.key === posts[0].key)).toBe(true)
    expect(x.bodies(A1)).toEqual(['A 대화'])
    expect(x.source.recordChatError).toHaveBeenCalledWith(A1, expect.objectContaining({ code: 'send_failed' }))

    await x.session.setScope(FLOW_A)
    await flushPromises()
    expect(x.session.state.activeChatId).toBe(A1)
    expect(x.session.state.pending).toMatchObject([{ body: '실패할 메시지', failed: true, idempotencyKey: posts[0].key }])
    expect(chatErrors(x)).toHaveLength(1)

    expect(await x.start.retry(x.session.state.pending[0].localId)).toBe(true)
    await flushPromises()
    expect(x.posts().at(-1)).toEqual({ chatId: A1, key: posts[0].key, body: '실패할 메시지' })
    expect(x.session.state.pending).toEqual([])
    expect(x.bodies(A1)).toEqual(['A 대화', '실패할 메시지'])
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['A 대화', '실패할 메시지'])
  })

  it('the recorded failure survives a reload (F5): a new session on A shows it', async () => {
    const x = await setup({ aChats: true })
    const answer = deferred()
    x.steps.push({ gate: answer.promise, outcome: 500 })
    for (let i = 0; i < SEND_RETRY_MAX; i++) x.steps.push({ outcome: 500 })
    const sending = x.start.send('실패')
    await x.session.setScope(FLOW_B)
    answer.resolve()
    expect(await sending).toBe(false)
    await flushPromises()
    x.session.dispose()
    const fresh = createConversationSession({ source: x.source, sleep: async () => undefined })
    await fresh.setScope(FLOW_A)
    await flushPromises()
    expect(fresh.state.activeChatId).toBe(A1)
    expect(fresh.state.activities.filter((a) => a.kind === 'status' && a.code === 'send_failed')).toHaveLength(1)
    fresh.dispose()
  })
})

describe('a new chat: the Flow changes before its first POST starts', () => {
  it('during the create: the chat made in A gets the message; B stays open and untouched', async () => {
    const x = await setup()
    expect(x.start.isDraft.value).toBe(true)
    const create = deferred()
    x.creates.push(create.promise)
    const sending = x.start.send('첫 메시지')
    await flushPromises()
    expect(x.source.postTurn).not.toHaveBeenCalled()
    await x.session.setScope(FLOW_B)
    await flushPromises()
    create.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expect(x.posts()).toEqual([{ chatId: made, key: expect.any(String), body: '첫 메시지' }])
    expect(x.bodies(made)).toEqual(['첫 메시지'])
    expectFlowBUntouched(x)
    expect(x.session.state.notice).toBeNull()

    await x.session.setScope(FLOW_A)
    await flushPromises()
    expect(x.session.state.activeChatId).toBe(made)
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['첫 메시지'])
    expect(x.session.state.pending).toEqual([])
    expect(x.chatsOf[FLOW_A.groupId]).toHaveLength(1) // one chat, no empty leftover
  })

  it('while the new chat opens (history load): the message still goes to it, never to B', async () => {
    const x = await setup()
    const history = deferred()
    const read = x.source.fetchTurns.getMockImplementation()!
    x.source.fetchTurns.mockImplementationOnce(async (chatId: string, query: Parameters<typeof read>[1]) => {
      await history.promise
      return read(chatId, query)
    })
    const sending = x.start.send('로딩 중 이동')
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expect(x.session.state.activeChatId).toBe(made) // opening
    expect(x.source.postTurn).not.toHaveBeenCalled()
    await x.session.setScope(FLOW_B)
    await flushPromises()
    history.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    expect(x.posts()).toEqual([{ chatId: made, key: expect.any(String), body: '로딩 중 이동' }])
    expect(x.bodies(made)).toEqual(['로딩 중 이동'])
    expectFlowBUntouched(x)
  })

  it('another chat picked in the same Flow during the create: the message goes to the new chat, not the open one', async () => {
    const x = await setup({ aChats: true })
    x.start.startNew()
    const create = deferred()
    x.creates.push(create.promise)
    const sending = x.start.send('새 채팅 첫 메시지')
    await flushPromises()
    await x.session.selectChat(A1)
    create.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expect(made).not.toBe(A1)
    expect(x.posts()).toEqual([{ chatId: made, key: expect.any(String), body: '새 채팅 첫 메시지' }])
    expect(x.bodies(A1)).toEqual(['A 대화'])
    expect(x.session.state.activeChatId).toBe(A1)
    expect(x.session.state.turns.map((t) => t.body)).toEqual(['A 대화'])
    expect(x.session.state.pending).toEqual([])
    // The Flow shown is the chat's own: its list is read again and shows the new chat.
    expect(x.session.state.chats.map((c) => c.id)).toContain(made)
  })

  it('back in A before the create answers: the chat is made once, gets the message, and is listed', async () => {
    const x = await setup()
    const create = deferred()
    x.creates.push(create.promise)
    const sending = x.start.send('왕복')
    await flushPromises()
    await x.session.setScope(FLOW_B)
    await flushPromises()
    await x.session.setScope(FLOW_A)
    await flushPromises()
    create.resolve()
    expect(await sending).toBe(true)
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expect(x.chatsOf[FLOW_A.groupId]).toHaveLength(1)
    expect(x.posts()).toEqual([{ chatId: made, key: expect.any(String), body: '왕복' }])
    expect(x.bodies(made)).toEqual(['왕복'])
    expect(x.session.state.chats.map((c) => c.id)).toEqual([made])
  })

  it('failure off screen: a failed bubble with retry on the new chat when back in A; the retry reuses the key; one turn', async () => {
    const x = await setup()
    const create = deferred()
    x.creates.push(create.promise)
    for (let i = 0; i <= SEND_RETRY_MAX; i++) x.steps.push({ outcome: 503 })
    const sending = x.start.send('실패한 첫 메시지')
    await flushPromises()
    await x.session.setScope(FLOW_B)
    await flushPromises()
    create.resolve()
    expect(await sending).toBe(false)
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expectFlowBUntouched(x)
    const posts = x.posts()
    expect(posts).toHaveLength(SEND_RETRY_MAX + 1)
    expect(posts.every((p) => p.chatId === made && p.key === posts[0].key)).toBe(true)
    expect(x.bodies(made)).toEqual([])
    expect(x.source.recordChatError).toHaveBeenCalledWith(made, expect.objectContaining({ code: 'send_failed' }))

    await x.session.setScope(FLOW_A)
    await flushPromises()
    expect(x.session.state.activeChatId).toBe(made)
    expect(x.session.state.pending).toMatchObject([{ body: '실패한 첫 메시지', failed: true, idempotencyKey: posts[0].key }])
    expect(chatErrors(x)).toHaveLength(1)
    expect(await x.start.retry(x.session.state.pending[0].localId)).toBe(true)
    await flushPromises()
    expect(x.posts().at(-1)).toEqual({ chatId: made, key: posts[0].key, body: '실패한 첫 메시지' })
    expect(x.bodies(made)).toEqual(['실패한 첫 메시지'])
    expect(x.session.state.pending).toEqual([])
  })

  it('a create that fails after the move sends nothing anywhere', async () => {
    const x = await setup()
    const create = deferred()
    x.creates.push(create.promise.then(() => { throw httpError(500) }))
    const sending = x.start.send('생성 실패')
    await flushPromises()
    await x.session.setScope(FLOW_B)
    create.resolve()
    expect(await sending).toBe(false)
    await flushPromises()
    expect(x.source.postTurn).not.toHaveBeenCalled()
    expectFlowBUntouched(x)
    expect(x.session.state.notice).toBeNull()
  })
})

describe('ConversationPanel: send in Flow A, move to Flow B at once, come back', () => {
  let wrapper: VueWrapper | null = null
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    localStorage.clear()
  })
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('the first message of a new chat is stored in A, shown there, and its draft is cleared', async () => {
    const x = server()
    const create = deferred()
    x.creates.push(create.promise)
    wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: FLOW_A.groupId, source: x.source }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
    await find('conv-input').setValue('패널에서 보낸 메시지')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    await wrapper.setProps({ groupId: FLOW_B.groupId })
    await flushPromises()
    create.resolve()
    await flushPromises()
    const made = x.chatsOf[FLOW_A.groupId][0].id
    expect(x.posts()).toEqual([{ chatId: made, key: expect.any(String), body: '패널에서 보낸 메시지' }])
    expect(wrapper.findAll('[data-test="tl-message"]').map((m) => m.find('.tl-text').text())).toEqual(B_TURNS)

    await wrapper.setProps({ groupId: FLOW_A.groupId })
    await flushPromises()
    expect(wrapper.findAll('[data-test="tl-message"]').map((m) => m.find('.tl-text').text())).toEqual(['패널에서 보낸 메시지'])
    expect((find('conv-input').element as HTMLTextAreaElement).value).toBe('')
    const kept = Array.from({ length: localStorage.length }, (_, i) => localStorage.getItem(localStorage.key(i)!))
    expect(kept).not.toContain('패널에서 보낸 메시지')
    expect(x.posts()).toHaveLength(1)
  })
})
