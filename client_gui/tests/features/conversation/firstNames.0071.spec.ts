import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createChatStart } from '@/features/conversation/chatStart'
import { createFakeSource, turn, httpError } from './fakeConversationSource'

const subscriptions = vi.hoisted(() => [] as any[])
vi.mock('@/features/project/projectEvents', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/features/project/projectEvents')>(),
  subscribeProjectEvents: (_id: string, handlers: any) => { subscriptions.push(handlers); return vi.fn() },
}))
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const CHAT = 'p.m.0001.0002-CH'
async function setup() {
  subscriptions.length = 0
  const fake = createFakeSource({ chats: [{ id: CHAT, title: '새 대화', updatedAt: null }],
    turns: { [CHAT]: [turn(1, 'user', '로그인 오류 수정')] } })
  fake.source.autoTitleChat.mockRejectedValueOnce(httpError(409, 'no AI reply yet', 'conversation_auto_title_no_message'))
  const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => '새 대화' })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, session, start }
}
describe('shared first names', () => {
  it('recovers an unflagged default chat when the first AI reply appears', async () => {
    const { session, source, data } = await setup()
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    data.turns[CHAT].push(turn(2, 'ai', '수정합니다'))
    session.startDraft()
    await session.selectChat(CHAT)
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledWith(CHAT, 'opus')
  })
  it('refreshes only this Flow after commit and reads again after reconnect', async () => {
    const { source } = await setup()
    const sub = subscriptions.find((s) => s.events.includes('group_view_refresh'))
    expect(sub).toBeTruthy()
    const before = source.listChats.mock.calls.length
    sub.event({ groupId: 'other', payload: { reason: 'first_names' } })
    await flushPromises()
    expect(source.listChats.mock.calls.length).toBe(before)
    sub.event({ groupId: SCOPE.groupId, payload: { reason: 'first_names' } })
    await flushPromises()
    expect(source.listChats.mock.calls.length).toBe(before + 1)
    sub.resync()
    await flushPromises()
    expect(source.listChats.mock.calls.length).toBe(before + 2)
  })
})
