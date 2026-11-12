import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createChatStart } from '@/features/conversation/chatStart'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { buildTimeline } from '@/features/conversation/timeline'
import type { ChatDocSettings, ChatDocSettingsPatch } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

registerAppMessages(i18n)

// flowgate.v02test.0025 — each chat keeps its own AI (and the run uses it), and a chat's AI context can start at a
// message of the user's choice (the checkpoint). Both are stored per chat on the server.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

async function setup(stored: Record<string, ChatDocSettings> = {}, init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' },
      { id: B, title: 'Git conflict', updatedAt: '2026-10-05T08:00:00Z' },
    ],
    turns: {
      [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z'), turn(2, 'ai', 'done', '2026-10-06T08:03:00Z'), turn(3, 'user', 'next', '2026-10-06T08:04:00Z')],
      [B]: [turn(1, 'user', 'other chat', '2026-10-05T08:00:00Z')],
    },
    settings: { ...createFakeSource().data.settings, sendAction: 'invoke_ai' as const },
    ...init,
  })
  const server: Record<string, ChatDocSettings> = { ...stored }
  const source = fake.source
  source.fetchChatDocSettings = vi.fn(async (chatId: string) => ({ providerId: null, checkpointSeq: null, ...server[chatId] }))
  source.saveChatDocSettings = vi.fn(async (chatId: string, patch: ChatDocSettingsPatch) => {
    const current = { providerId: null, checkpointSeq: null, ...server[chatId] }
    if ('provider_id' in patch) current.providerId = patch.provider_id ?? null
    if ('context_checkpoint_seq' in patch) current.checkpointSeq = patch.context_checkpoint_seq ?? null
    server[chatId] = current
    return { ...current }
  })
  const session = createConversationSession({ source, sleep: async () => undefined })
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, source, server, session }
}

describe('v02test.0025 each chat keeps its own AI', () => {
  it('opens each chat on its stored AI, and a chat without one on my default AI', async () => {
    const { session } = await setup({ [A]: { providerId: 'sonnet', checkpointSeq: null } })
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.selectedProviderId).toBe('sonnet')
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus') // B has no pick: my default, not A's AI
  })

  it('a pick is saved to that chat only and comes back when the chat is opened again', async () => {
    const { session, source, server } = await setup()
    session.selectProvider('sonnet')
    await flushPromises()
    expect(source.saveChatDocSettings).toHaveBeenCalledWith(A, { provider_id: 'sonnet' })
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('opus')
    expect(server[B]).toBeUndefined()
    await session.selectChat(A)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('sonnet')
  })

  it('the run of a send uses the chat\'s own AI', async () => {
    const { session, source } = await setup({ [B]: { providerId: 'sonnet', checkpointSeq: null } })
    await session.selectChat(B)
    await flushPromises()
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String))
  })

  it('a stored AI that is no longer offered falls back to my default AI', async () => {
    const { session } = await setup({ [A]: { providerId: 'ghost', checkpointSeq: null } })
    expect(session.state.selectedProviderId).toBe('opus')
  })

  it('a new chat starts on my default AI and keeps the AI picked in its draft', async () => {
    const { session, source, server } = await setup({ [A]: { providerId: 'sonnet', checkpointSeq: null } })
    const start = createChatStart({ session, source, defaultTitle: () => '새 대화' })
    start.startNew()
    expect(session.state.selectedProviderId).toBe('opus')
    session.selectProvider('sonnet')
    expect(source.saveChatDocSettings).not.toHaveBeenCalled() // nothing to store until the chat exists
    expect(await start.send('hello')).toBe(true)
    await flushPromises()
    const chatId = session.state.activeChatId!
    expect(server[chatId]?.providerId).toBe('sonnet')
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(source.startChatRun).toHaveBeenCalledWith(chatId, 'sonnet', expect.any(String))
  })

  it('a source without per-chat settings keeps one selection (behaviour before)', async () => {
    const fake = createFakeSource({ chats: [{ id: A, title: 'a', updatedAt: null }, { id: B, title: 'b', updatedAt: null }], turns: { [A]: [], [B]: [] } })
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    await session.setScope(SCOPE)
    await flushPromises()
    session.selectProvider('sonnet')
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('sonnet')
    expect(session.supportsChatDocSettings).toBe(false)
  })
})

describe('v02test.0025 context checkpoint', () => {
  it('loads, sets and clears the open chat\'s checkpoint', async () => {
    const { session, source } = await setup({ [A]: { providerId: null, checkpointSeq: 2 } })
    expect(session.state.checkpointSeq).toBe(2)
    await session.setCheckpoint(3)
    expect(source.saveChatDocSettings).toHaveBeenLastCalledWith(A, { context_checkpoint_seq: 3 })
    expect(session.state.checkpointSeq).toBe(3)
    await session.selectChat(B)
    await flushPromises()
    expect(session.state.checkpointSeq).toBeNull() // another chat, its own (none)
    await session.selectChat(A)
    await flushPromises()
    expect(session.state.checkpointSeq).toBe(3)
    await session.setCheckpoint(null)
    expect(session.state.checkpointSeq).toBeNull()
  })

  it('a refused save keeps the old checkpoint and says so', async () => {
    const { session, source } = await setup({ [A]: { providerId: null, checkpointSeq: 2 } })
    source.saveChatDocSettings.mockRejectedValueOnce(new Error('nope'))
    await session.setCheckpoint(3)
    expect(session.state.checkpointSeq).toBe(2)
    expect(session.state.notice?.code).toBe('checkpoint_failed')
  })

  it('the timeline marks the checkpoint message and offers set / clear', async () => {
    const turns = [turn(1, 'user', 'look', null), turn(2, 'ai', 'done', null), turn(3, 'user', 'next', null)]
    const items = buildTimeline(turns, [], [], 0)
    const wrapper = mount(ConversationTimeline, {
      props: { items, status: 'ready', hasMoreBefore: false, loadingOlder: false, deciding: [], emptyText: '', chatId: A,
        checkpointSeq: 2, canCheckpoint: true },
      global: { plugins: [i18n] },
    })
    const line = wrapper.find('[data-test="conv-checkpoint"]')
    expect(line.exists()).toBe(true)
    // The line sits right before message 2.
    expect(line.element.nextElementSibling?.getAttribute('data-seq')).toBe('2')
    const buttons = wrapper.findAll('[data-test="tl-checkpoint"]')
    expect(buttons).toHaveLength(3)
    expect(buttons[1].attributes('aria-pressed')).toBe('true')
    await buttons[2].trigger('click')
    await buttons[1].trigger('click')
    await line.find('button').trigger('click')
    expect(wrapper.emitted('checkpoint')).toEqual([[3], [null], [null]])
    wrapper.unmount()
  })

  it('a checkpoint older than the loaded messages is shown at the top', async () => {
    const items = buildTimeline([turn(40, 'user', 'late', null)], [], [], 0)
    const wrapper = mount(ConversationTimeline, {
      props: { items, status: 'ready', hasMoreBefore: true, loadingOlder: false, deciding: [], emptyText: '', chatId: A,
        checkpointSeq: 12, canCheckpoint: true },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('[data-test="conv-checkpoint"]').text()).toContain('12')
    wrapper.unmount()
  })
})
