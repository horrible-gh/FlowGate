import { describe, expect, it } from 'vitest'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, turn } from './fakeConversationSource'

const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
const initial = { key: 'ai:real', kind: 'ai', displayName: 'Actual model', lastReadSeq: 1 }

describe('current participant metadata (0154)', () => {
  it('refreshes additions, removals and an authoritative empty list without changing messages', async () => {
    const { source, data } = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }], turns: { [A]: [turn(1, 'user', 'hello')] }, participants: [initial],
    })
    const session = createConversationSession({ source })
    try {
      await session.setScope({ projectId: 'p', groupId: 'p.m.0001' })
      expect(session.state.participants).toEqual([initial])
      data.participants = [initial, { key: 'u:new', kind: 'user', displayName: 'New person', lastReadSeq: 2 }]
      await session.selectChat(A)
      expect(source.fetchTurns).toHaveBeenLastCalledWith(A, expect.objectContaining({ includeHead: true }))
      expect(session.state.participants).toHaveLength(2)
      data.participants = []
      await session.selectChat(A)
      expect(session.state.participants).toEqual([])
      expect(session.state.turns.map((item) => item.body)).toEqual(['hello'])
    } finally { session.dispose() }
  })

  it('does not erase metadata on older pages and replaces it on chat changes and remount', async () => {
    const { source } = createFakeSource({
      chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'first'), turn(2, 'ai', 'second')], [B]: [] }, participants: [initial],
    })
    const fetch = source.fetchTurns.getMockImplementation()!
    source.fetchTurns.mockImplementation(async (chatId: string, query: { includeHead?: boolean; beforeSeq?: number }) => {
      const page = await fetch(chatId, query)
      return { ...page, participants: query.includeHead && chatId === A ? [initial] : [] }
    })
    const session = createConversationSession({ source })
    try {
      await session.setScope({ projectId: 'p', groupId: 'p.m.0001' })
      session.state.turns = [turn(2, 'ai', 'second')]
      await session.loadOlder()
      expect(session.state.participants).toEqual([initial])
      await session.selectChat(B)
      expect(session.state.participants).toEqual([])
      await session.selectChat(A)
      expect(session.state.participants).toEqual([initial])
    } finally { session.dispose() }
    const reloaded = createConversationSession({ source })
    try {
      await reloaded.setScope({ projectId: 'p', groupId: 'p.m.0001' })
      expect(reloaded.state.participants).toEqual([initial])
    } finally { reloaded.dispose() }
  })
})
