import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession, readStoredSends } from '@/features/conversation/conversationSession'
import { draftUserId, type DraftSendSnapshot } from '@/features/conversation/drafts'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02test.0029 T0005 — the ids of the message images ride the send pipeline (bubble, retry, F5 record, first
// message of a new chat) and reach POST /conversation/turn as `images: [image_id…]`, under one idempotency key.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const NEW_CHAT = 'p.m.0001.0010-CH'
const ID1 = `img_${'1'.repeat(32)}`
const ID2 = `img_${'2'.repeat(32)}`

async function setup(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' }],
    turns: { [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z')] },
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session, start }
}

const snapshotOf = (images: string[], body = 'look at this'): DraftSendSnapshot => ({
  owner: { userId: draftUserId(), projectId: 'p', groupId: SCOPE.groupId, intentId: '', chatId: A }, body, revision: 0, images,
})

beforeEach(() => localStorage.clear())

describe('message images in the send', () => {
  it('posts the image ids with the turn in the chat that is open', async () => {
    const { session, source } = await setup()
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID1, ID2]))).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    expect(source.postTurn).toHaveBeenCalledWith(A, expect.objectContaining({ body: 'look at this', images: [ID1, ID2] }))
  })

  it('a text message posts no images field', async () => {
    const { session, source } = await setup()
    await session.send('just text')
    await flushPromises()
    expect(source.postTurn.mock.calls[0][1]).not.toHaveProperty('images')
  })

  it('keeps the ids on the pending bubble and in the F5 record while the server has not answered', async () => {
    const { session, source } = await setup()
    let release!: () => void
    source.postTurn.mockImplementationOnce((_id: string, input: { body: string }) => new Promise((resolve) => {
      release = () => resolve({ turn: turn(2, 'user', input.body, '2026-10-06T10:00:00Z'), headSeq: 2 })
    }))
    const sending = session.send('look at this', undefined, undefined, snapshotOf([ID1]))
    expect(session.state.pending[0]).toMatchObject({ body: 'look at this', images: [ID1] })
    expect(readStoredSends(draftUserId(), 'p')[0]).toMatchObject({ chatId: A, images: [ID1] })
    release()
    expect(await sending).toBe(true)
    expect(readStoredSends(draftUserId(), 'p')).toEqual([])
  })

  it('a retry after a failed send posts the same images under the same idempotency key', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID1]))).toBe(false)
    const failed = session.state.pending[0]
    expect(failed).toMatchObject({ failed: true, images: [ID1] })
    expect(await session.retry(failed.localId)).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(2)
    expect(source.postTurn.mock.calls[1][1]).toMatchObject({ idempotencyKey: source.postTurn.mock.calls[0][1].idempotencyKey, images: [ID1] })
  })

  it('sending the same text again after a failure reuses the failed bubble with its images', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('look at this', undefined, undefined, snapshotOf([ID1]))
    await session.send('look at this', undefined, undefined, snapshotOf([ID1]))
    await flushPromises()
    expect(source.postTurn.mock.calls[1][1]).toMatchObject({ idempotencyKey: source.postTurn.mock.calls[0][1].idempotencyKey, images: [ID1] })
  })

  it('the same text with another image after a refused send posts the current image, under a new key (A → B)', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID1]))).toBe(false)
    const refused = session.state.pending[0]
    expect(refused).toMatchObject({ failed: true, images: [ID1] })
    // the user took A out and attached B, the text stayed
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID2]))).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(2)
    const second = source.postTurn.mock.calls[1][1]
    expect(second.images).toEqual([ID2])
    expect(second.idempotencyKey).not.toBe(source.postTurn.mock.calls[0][1].idempotencyKey)
    // the failed bubble of the old attempt is gone, and no stored F5 record of it is left to send A again
    expect(session.state.pending.some((p) => p.localId === refused.localId)).toBe(false)
    expect(session.state.pending.filter((p) => p.failed)).toEqual([])
    expect(readStoredSends(draftUserId(), 'p')).toEqual([])
  })

  it('the same text after a refused send, with its image taken out, posts no images (A → none)', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('look at this', undefined, undefined, snapshotOf([ID1]))
    const noImages: DraftSendSnapshot = { ...snapshotOf([]), images: undefined }
    expect(await session.send('look at this', undefined, undefined, noImages)).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(2)
    expect(source.postTurn.mock.calls[1][1]).not.toHaveProperty('images')
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).not.toBe(source.postTurn.mock.calls[0][1].idempotencyKey)
    expect(session.state.pending.filter((p) => p.failed)).toEqual([])
  })

  it('the same text after a failed send, with an image added, posts the current images (none → A)', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('look at this')
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID1]))).toBe(true)
    await flushPromises()
    expect(source.postTurn.mock.calls[1][1]).toMatchObject({ images: [ID1] })
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).not.toBe(source.postTurn.mock.calls[0][1].idempotencyKey)
  })

  it('an unanswered send (no refusal) followed by the same text with another image also sends the current image', async () => {
    const { session, source } = await setup()
    const stored = source.postTurn.getMockImplementation()!
    let down = true
    source.postTurn.mockImplementation((...args: unknown[]) => down ? Promise.reject(httpError(503, 'down')) : (stored as (...a: unknown[]) => Promise<unknown>)(...args))
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID1]))).toBe(false)
    expect(readStoredSends(draftUserId(), 'p')).toHaveLength(1)
    down = false
    const before = source.postTurn.mock.calls.length
    expect(await session.send('look at this', undefined, undefined, snapshotOf([ID2]))).toBe(true)
    await flushPromises()
    expect(source.postTurn.mock.calls[before][1].images).toEqual([ID2])
    expect(session.state.pending.filter((p) => p.failed)).toEqual([])
    expect(readStoredSends(draftUserId(), 'p')).toEqual([])
  })

  it('a refused send is tried again with its bubble button using its own images (the bubble keeps A until replaced)', async () => {
    const { session, source } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('look at this', undefined, undefined, snapshotOf([ID1, ID2]))
    // the same images again, in the same order: the failed bubble is the message
    await session.send('look at this', undefined, undefined, snapshotOf([ID1, ID2]))
    expect(source.postTurn.mock.calls[1][1]).toMatchObject({ idempotencyKey: source.postTurn.mock.calls[0][1].idempotencyKey, images: [ID1, ID2] })
    // the same ids in another order are another message
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('other', undefined, undefined, snapshotOf([ID1, ID2], 'other'))
    await session.send('other', undefined, undefined, snapshotOf([ID2, ID1], 'other'))
    expect(source.postTurn.mock.calls[3][1].images).toEqual([ID2, ID1])
    expect(source.postTurn.mock.calls[3][1].idempotencyKey).not.toBe(source.postTurn.mock.calls[2][1].idempotencyKey)
  })
})

describe('first message of a new chat (no chat yet)', () => {
  it('creates the chat, then posts the first message with its images', async () => {
    const { start, source, session } = await setup({ chats: [], turns: {} })
    expect(start.isDraft.value).toBe(true)
    const owner = { userId: draftUserId(), ...SCOPE, intentId: start.draftIntentId.value!, chatId: null }
    expect(await start.send('첫 메시지', { owner, body: '첫 메시지', revision: 0, images: [ID1] })).toBe(true)
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledTimes(1)
    expect(source.postTurn).toHaveBeenCalledWith(NEW_CHAT, expect.objectContaining({ body: '첫 메시지', images: [ID1] }))
    expect(session.state.activeChatId).toBe(NEW_CHAT)
  })

  it('shows the first message at once and keeps its images in the F5 record until the chat is made', async () => {
    const { start, source, session } = await setup({ chats: [], turns: {} })
    let finishCreate!: () => void
    const created = source.createChat.getMockImplementation()!
    source.createChat.mockImplementationOnce((...args: unknown[]) => new Promise((resolve) => {
      finishCreate = () => resolve((created as (...a: unknown[]) => Promise<unknown>)(...args))
    }))
    const owner = { userId: draftUserId(), ...SCOPE, intentId: start.draftIntentId.value!, chatId: null }
    const sending = start.send('첫 메시지', { owner, body: '첫 메시지', revision: 0, images: [ID1, ID2] })
    await flushPromises()
    expect(session.state.pending[0]).toMatchObject({ body: '첫 메시지', images: [ID1, ID2] })
    expect(readStoredSends(draftUserId(), 'p')[0]).toMatchObject({ chatId: null, images: [ID1, ID2] })
    finishCreate()
    expect(await sending).toBe(true)
    expect(source.postTurn).toHaveBeenCalledWith(NEW_CHAT, expect.objectContaining({ images: [ID1, ID2] }))
  })

  it('a failed first message is tried again with its images', async () => {
    const { start, source, session } = await setup({ chats: [], turns: {} })
    source.createChat.mockRejectedValueOnce(httpError(500, 'down'))
    const owner = { userId: draftUserId(), ...SCOPE, intentId: start.draftIntentId.value!, chatId: null }
    expect(await start.send('첫 메시지', { owner, body: '첫 메시지', revision: 0, images: [ID1] })).toBe(false)
    await flushPromises()
    const failed = session.state.pending.find((p) => p.failed)
    expect(failed).toMatchObject({ body: '첫 메시지', images: [ID1] })
    expect(source.postTurn).not.toHaveBeenCalled()
    expect(await start.retry(failed!.localId)).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledWith(NEW_CHAT, expect.objectContaining({ body: '첫 메시지', images: [ID1] }))
  })

  it('a failed first message sent again with another image posts the current image, not the old one (A → B)', async () => {
    const { start, source, session } = await setup({ chats: [], turns: {} })
    source.createChat.mockRejectedValueOnce(httpError(422, 'bad'))
    const owner = { userId: draftUserId(), ...SCOPE, intentId: start.draftIntentId.value!, chatId: null }
    expect(await start.send('첫 메시지', { owner, body: '첫 메시지', revision: 0, images: [ID1] })).toBe(false)
    await flushPromises()
    expect(session.state.pending.find((p) => p.failed)).toMatchObject({ images: [ID1] })
    expect(await start.send('첫 메시지', { owner, body: '첫 메시지', revision: 0, images: [ID2] })).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    expect(source.postTurn).toHaveBeenCalledWith(NEW_CHAT, expect.objectContaining({ body: '첫 메시지', images: [ID2] }))
    expect(session.state.pending.filter((p) => p.failed)).toEqual([])
  })
})
