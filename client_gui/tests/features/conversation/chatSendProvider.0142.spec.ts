import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0142 T#1 — the AI chosen when a chat message is sent is the AI of that message's run:
// a change of the picker while the turn is stored, after a failed try, or while a new chat is created
// does not reach `startChatRun`; and an empty / unusable choice ends with a notice, never a default AI.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

const invokeSettings = () => ({ ...createFakeSource().data.settings, sendAction: 'invoke_ai' as const })

async function setup(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' },
      { id: B, title: 'Git conflict', updatedAt: '2026-10-05T08:00:00Z' },
    ],
    turns: {
      [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z'), turn(2, 'ai', 'done', '2026-10-06T08:03:00Z')],
      [B]: [turn(1, 'user', 'other chat', '2026-10-05T08:00:00Z')],
    },
    settings: invokeSettings(),
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session }
}

/** Holds the next postTurn until `release()`; the turn is then stored as seq 3. */
function holdNextPost(source: Awaited<ReturnType<typeof setup>>['source']) {
  let release!: () => void
  source.postTurn.mockImplementationOnce(
    (_chatId: string, input: { body: string }) =>
      new Promise((resolve) => {
        release = () => resolve({ turn: turn(3, 'user', input.body, '2026-10-06T10:00:00Z'), headSeq: 3 })
      }),
  )
  return () => release()
}

describe('0142 chat send pins the AI of the message', () => {
  it('1: runs on the selected AI when nothing changes', async () => {
    const { session, source } = await setup()
    session.selectProvider('opus')
    expect(await session.send('a')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'opus', expect.any(String))
  })

  it('2: a change of the AI while the turn is stored does not change the run', async () => {
    const { session, source } = await setup()
    session.selectProvider('opus')
    const release = holdNextPost(source)
    const sending = session.send('a')
    expect(session.state.pending[0]).toMatchObject({ providerId: 'opus' })
    session.selectProvider('sonnet')
    release()
    expect(await sending).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'opus', expect.any(String))
    expect(source.startChatRun).not.toHaveBeenCalledWith(expect.anything(), 'sonnet', expect.anything())
    // The picker keeps what the user chose afterwards; only the message's own run is pinned.
    expect(session.state.selectedProviderId).toBe('sonnet')
  })

  it('3: a retry after a failed send runs on the AI of the first send, not on the later choice', async () => {
    const { session, source } = await setup()
    session.selectProvider('opus')
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    expect(await session.send('a')).toBe(false)
    expect(source.startChatRun).not.toHaveBeenCalled()
    const failed = session.state.pending[0]
    expect(failed).toMatchObject({ failed: true, providerId: 'opus' })
    session.selectProvider('sonnet')
    expect(await session.retry(failed.localId)).toBe(true)
    await flushPromises()
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).toBe(failed.idempotencyKey)
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'opus', expect.any(String))
  })

  it('4: leaving the chat before the turn is stored drops the run (generation guard kept)', async () => {
    const { session, source } = await setup()
    session.selectProvider('opus')
    const release = holdNextPost(source)
    const sending = session.send('a')
    await session.selectChat(B)
    release()
    expect(await sending).toBe(true)
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.turns.map((t) => t.body)).toEqual(['other chat'])
  })

  it('2-b: a new chat made by the first send runs on the AI chosen before the chat was created', async () => {
    const { session, source } = await setup({ chats: [], turns: {} })
    const start = createChatStart({ session, source, defaultTitle: () => '새 대화' })
    expect(start.isDraft.value).toBe(true)
    session.selectProvider('opus')
    // createChat stays pending until released; the user changes the AI meanwhile.
    const create = source.createChat.getMockImplementation()! as (...a: unknown[]) => Promise<unknown>
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    source.createChat.mockImplementationOnce(async (...args: unknown[]) => {
      await gate
      return create(...args)
    })
    const sending = start.send('hello')
    await flushPromises()
    expect(start.starting.value).toBe(true)
    session.selectProvider('sonnet')
    release()
    expect(await sending).toBe(true)
    await flushPromises()
    const chatId = session.state.activeChatId!
    expect(source.postTurn).toHaveBeenCalledWith(chatId, expect.objectContaining({ body: 'hello' }))
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).toHaveBeenCalledWith(chatId, 'opus', expect.any(String))
    expect(session.state.selectedProviderId).toBe('sonnet')
  })

  describe('2-c: no usable AI ends with a notice, never with a default AI', () => {
    const cases: Array<[string, string]> = [
      ['empty id', ''],
      ['id missing from the provider list', 'ghost'],
    ]
    for (const [name, providerId] of cases) {
      it(name, async () => {
        const { session, source } = await setup()
        expect(await session.send('a', { providerId })).toBe(true)
        await flushPromises()
        // The message itself is stored; only the AI call is refused.
        expect(source.postTurn).toHaveBeenCalledTimes(1)
        expect(session.state.turns.at(-1)?.body).toBe('a')
        expect(source.startChatRun).not.toHaveBeenCalled()
        expect(session.state.notice).toMatchObject({ tone: 'warning', code: 'no_provider' })
        expect(session.state.runId).toBeNull()
      })
    }

    it('empty provider list (selection empty)', async () => {
      const { session, source } = await setup()
      source.listProviders.mockResolvedValue({ providers: [], defaultProviderId: null })
      // Another Flow reloads the list; the selection ends up empty. Chat A is opened again by hand.
      await session.setScope({ projectId: 'q', groupId: 'q.m.0001' })
      await flushPromises()
      expect(session.state.selectedProviderId).toBe('')
      await session.selectChat(A)
      expect(await session.send('a')).toBe(true)
      await flushPromises()
      expect(session.state.turns.at(-1)?.body).toBe('a')
      expect(source.startChatRun).not.toHaveBeenCalled()
      expect(session.state.notice).toMatchObject({ code: 'no_provider' })
      expect(session.state.runId).toBeNull()
    })

    it('the [AI 호출] button still uses the selection of the moment it is pressed', async () => {
      const { session, source } = await setup()
      session.selectProvider('sonnet')
      await session.invokeAi()
      expect(source.startChatRun).toHaveBeenCalledWith(A, 'sonnet', expect.any(String))
    })
  })
})
