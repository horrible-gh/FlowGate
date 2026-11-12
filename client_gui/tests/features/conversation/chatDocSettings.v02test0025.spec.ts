import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createChatStart } from '@/features/conversation/chatStart'
import { ConversationPanel } from '@/features/conversation'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { buildTimeline } from '@/features/conversation/timeline'
import type { ChatDocSettings, ChatDocSettingsPatch } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

registerAppMessages(i18n)

// flowgate.v02test.0025 — each chat keeps its own AI (and the run uses it), and under the context setting 체크포인트 사용
// a chat's AI context starts at a message of the user's choice (⋯ menu: from this message / from the next message).
// Both are stored per chat on the server.
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
  const empty: ChatDocSettings = { providerId: null, checkpointSeq: null, checkpointKind: null }
  source.fetchChatDocSettings = vi.fn(async (chatId: string) => ({ ...empty, ...server[chatId] }))
  source.saveChatDocSettings = vi.fn(async (chatId: string, patch: ChatDocSettingsPatch) => {
    const current: ChatDocSettings = { ...empty, ...server[chatId] }
    if ('provider_id' in patch) current.providerId = patch.provider_id ?? null
    if ('context_checkpoint_seq' in patch) {
      current.checkpointSeq = patch.context_checkpoint_seq ?? null
      current.checkpointKind = current.checkpointSeq == null ? null : patch.context_checkpoint_kind ?? 'include'
    }
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
    const { session } = await setup({ [A]: { providerId: 'sonnet', checkpointSeq: null, checkpointKind: null } })
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
    const { session, source } = await setup({ [B]: { providerId: 'sonnet', checkpointSeq: null, checkpointKind: null } })
    await session.selectChat(B)
    await flushPromises()
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(B, 'sonnet', expect.any(String))
  })

  it('a stored AI that is no longer offered falls back to my default AI', async () => {
    const { session } = await setup({ [A]: { providerId: 'ghost', checkpointSeq: null, checkpointKind: null } })
    expect(session.state.selectedProviderId).toBe('opus')
  })

  it('a new chat starts on my default AI and keeps the AI picked in its draft', async () => {
    const { session, source, server } = await setup({ [A]: { providerId: 'sonnet', checkpointSeq: null, checkpointKind: null } })
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
  it('loads, sets (from this message / from the next) and clears the open chat\'s checkpoint', async () => {
    const { session, source } = await setup({ [A]: { providerId: null, checkpointSeq: 2, checkpointKind: 'include' } })
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([2, 'include'])
    await session.setCheckpoint(3, 'exclude')
    expect(source.saveChatDocSettings).toHaveBeenLastCalledWith(A, { context_checkpoint_seq: 3, context_checkpoint_kind: 'exclude' })
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([3, 'exclude'])
    await session.setCheckpoint(3, 'include') // same message, other kind: saved too
    expect(source.saveChatDocSettings).toHaveBeenLastCalledWith(A, { context_checkpoint_seq: 3, context_checkpoint_kind: 'include' })
    expect(session.state.checkpointKind).toBe('include')
    await session.selectChat(B)
    await flushPromises()
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([null, null]) // another chat, its own (none)
    await session.selectChat(A)
    await flushPromises()
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([3, 'include'])
    await session.setCheckpoint(null)
    expect(source.saveChatDocSettings).toHaveBeenLastCalledWith(A, { context_checkpoint_seq: null })
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([null, null])
  })

  it('a refused save keeps the old checkpoint and says so', async () => {
    const { session, source } = await setup({ [A]: { providerId: null, checkpointSeq: 2, checkpointKind: 'include' } })
    source.saveChatDocSettings.mockRejectedValueOnce(new Error('nope'))
    await session.setCheckpoint(3, 'exclude')
    expect([session.state.checkpointSeq, session.state.checkpointKind]).toEqual([2, 'include'])
    expect(session.state.notice?.code).toBe('checkpoint_failed')
  })

  function mountTimeline(props: Record<string, unknown>, turns = [turn(1, 'user', 'look', null), turn(2, 'ai', 'done', null), turn(3, 'user', 'next', null)]) {
    const items = buildTimeline(turns, [], [], 0)
    return mount(ConversationTimeline, {
      props: { items, status: 'ready', hasMoreBefore: false, loadingOlder: false, deciding: [], emptyText: '', chatId: A, ...props },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
  }

  it('each message has a ⋯ menu right of its copy button: from this message / from the next / clear', async () => {
    const wrapper = mountTimeline({ checkpointSeq: 2, checkpointKind: 'include', canCheckpoint: true })
    const menus = wrapper.findAll('[data-test="tl-more"]')
    expect(menus).toHaveLength(3)
    // Right of the copy button.
    expect(menus[0].element.closest('.tlm')?.previousElementSibling?.getAttribute('data-test')).toBe('tl-copy')
    expect(menus[1].classes()).toContain('is-on')
    // Message 3: no checkpoint here, so no clear item.
    await menus[2].trigger('click')
    expect(wrapper.find('[data-test="tl-checkpoint-clear"]').exists()).toBe(false)
    await wrapper.find('[data-test="tl-checkpoint-exclude"]').trigger('click')
    expect(wrapper.find('[data-test="tl-more-menu"]').exists()).toBe(false)
    // Message 2 (the checkpoint, include): the current kind is checked; clear is offered.
    await menus[1].trigger('click')
    expect(wrapper.find('[data-test="tl-checkpoint-include"]').attributes('aria-checked')).toBe('true')
    await wrapper.find('[data-test="tl-checkpoint-clear"]').trigger('click')
    await menus[1].trigger('click')
    await wrapper.find('[data-test="tl-checkpoint-include"]').trigger('click') // already include: nothing to save
    await menus[0].trigger('click')
    await wrapper.find('[data-test="tl-checkpoint-include"]').trigger('click')
    expect(wrapper.emitted('checkpoint')).toEqual([[3, 'exclude'], [null], [1, 'include']])
    wrapper.unmount()
  })

  it('the line sits where sending starts: above the message (from this) or below it (from the next)', async () => {
    const include = mountTimeline({ checkpointSeq: 2, checkpointKind: 'include', canCheckpoint: true })
    const above = include.find('[data-test="conv-checkpoint"]')
    expect(above.attributes('data-kind')).toBe('include')
    expect(above.element.nextElementSibling?.getAttribute('data-seq')).toBe('2')
    await above.find('button').trigger('click')
    expect(include.emitted('checkpoint')).toEqual([[null]])
    include.unmount()

    const exclude = mountTimeline({ checkpointSeq: 2, checkpointKind: 'exclude', canCheckpoint: true })
    const below = exclude.find('[data-test="conv-checkpoint"]')
    expect(below.attributes('data-kind')).toBe('exclude')
    expect(below.element.previousElementSibling?.getAttribute('data-seq')).toBe('2')
    expect(below.element.nextElementSibling?.getAttribute('data-seq')).toBe('3')
    exclude.unmount()
  })

  it('the line reads the same both ways (▼ 여기부터 전송됩니다); the messages above it are dimmed', async () => {
    i18n.global.locale.value = 'ko'
    const include = mountTimeline({ checkpointSeq: 2, checkpointKind: 'include', canCheckpoint: true })
    expect(include.find('[data-test="conv-checkpoint"] span').text()).toBe('▼ 여기부터 전송됩니다')
    expect(include.findAll('[data-test="tl-message"]').map((m) => m.classes().includes('is-unsent'))).toEqual([true, false, false])
    include.unmount()
    const exclude = mountTimeline({ checkpointSeq: 2, checkpointKind: 'exclude', canCheckpoint: true })
    expect(exclude.find('[data-test="conv-checkpoint"] span').text()).toBe('▼ 여기부터 전송됩니다')
    expect(exclude.findAll('[data-test="tl-message"]').map((m) => m.classes().includes('is-unsent'))).toEqual([true, true, false])
    exclude.unmount()
  })

  it('a checkpoint message that is not on screen draws no line anywhere', async () => {
    const turns = [turn(40, 'user', 'late', null), turn(41, 'ai', 'later', null)]
    const wrapper = mountTimeline({ hasMoreBefore: true, checkpointSeq: 12, checkpointKind: 'include', canCheckpoint: true }, turns)
    expect(wrapper.find('[data-test="conv-checkpoint"]').exists()).toBe(false)
    expect(wrapper.findAll('.is-unsent')).toHaveLength(0) // every loaded message is after the checkpoint: all are sent
    wrapper.unmount()
  })

  it('the ⋯ items carry where the line goes (⤒ above / ⤓ below / ✕ clear); ✓ sits at the right', async () => {
    const wrapper = mountTimeline({ checkpointSeq: 2, checkpointKind: 'exclude', canCheckpoint: true })
    await wrapper.findAll('[data-test="tl-more"]')[1].trigger('click')
    const icon = (test: string) => wrapper.find(`[data-test="${test}"] .tlm-icon`).text()
    expect([icon('tl-checkpoint-include'), icon('tl-checkpoint-exclude'), icon('tl-checkpoint-clear')]).toEqual(['⤒', '⤓', '✕'])
    const exclude = wrapper.find('[data-test="tl-checkpoint-exclude"]')
    expect(exclude.element.lastElementChild?.textContent).toBe('✓')
    expect(wrapper.find('[data-test="tl-checkpoint-include"] .tlm-check').text()).toBe('')
    wrapper.unmount()
  })

  it('without 체크포인트 사용 there is no ⋯ menu and no line', async () => {
    const wrapper = mountTimeline({ checkpointSeq: null, checkpointKind: null, canCheckpoint: false })
    expect(wrapper.find('[data-test="tl-more"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="conv-checkpoint"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-test="tl-copy"]')).toHaveLength(3) // the copy button is untouched
    wrapper.unmount()
  })
})

describe('v02test.0025 context setting 체크포인트 사용', () => {
  async function mountPanel(stored: Record<string, ChatDocSettings>, contextMode: 'recent' | 'checkpoint') {
    const { source, data } = await setup(stored)
    data.settings.contextMode = contextMode
    source.listProviders.mockResolvedValue({ providers: [{ id: 'opus', name: 'Opus' }, { id: 'sonnet', name: 'Sonnet' }], defaultProviderId: 'opus' })
    const wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId: 'p.m.0001', source },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
    return { wrapper, source, data }
  }

  it('is the last choice of the ⚙ context list; saving it keeps the turn count and shows the ⋯ menu', async () => {
    i18n.global.locale.value = 'ko'
    const { wrapper, source } = await mountPanel({ [A]: { providerId: null, checkpointSeq: 2, checkpointKind: 'exclude' } }, 'recent')
    // [recent]: the stored checkpoint is kept but not drawn, and no ⋯ menu.
    expect(wrapper.find('[data-test="tl-more"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="conv-checkpoint"]').exists()).toBe(false)

    await wrapper.find('[data-test="conv-settings-btn"]').trigger('click')
    const select = wrapper.find('[data-test="conv-setting-context"]')
    const options = select.findAll('option')
    expect(options.map((o) => o.attributes('value'))).toEqual(['5', '10', '15', '20', '30', 'custom', 'all', 'checkpoint'])
    expect(options[options.length - 1].text()).toBe('체크포인트 사용')
    await select.setValue('checkpoint')
    await wrapper.find('[data-test="conv-settings-save"]').trigger('click')
    await flushPromises()
    const patch = source.saveChatSettings.mock.calls.at(-1)![0] as Record<string, unknown>
    expect(patch.context_mode).toBe('checkpoint')
    expect('context_turns' in patch).toBe(false)

    expect(wrapper.findAll('[data-test="tl-more"]').length).toBeGreaterThan(0)
    const line = wrapper.find('[data-test="conv-checkpoint"]')
    expect(line.attributes('data-kind')).toBe('exclude')
    expect(line.text()).toContain('여기부터 전송됩니다')

    // Reopening shows 체크포인트 사용 selected.
    await wrapper.find('[data-test="conv-settings-btn"]').trigger('click')
    expect((wrapper.find('[data-test="conv-setting-context"]').element as HTMLSelectElement).value).toBe('checkpoint')
    wrapper.unmount()
  })
})

describe('v02test.0025 the checkpoint message is always loaded under 체크포인트 사용', () => {
  // A 120-message chat: opening it reads the newest 50 (71~120); [이전 대화] reads 30 more each time.
  const HEAD = 120
  const ALL = Array.from({ length: HEAD }, (_, i) => turn(i + 1, i % 2 ? 'ai' : 'user', `m${i + 1}`, null))

  async function setupLong(contextMode: 'recent' | 'checkpoint', checkpoint: ChatDocSettings | null) {
    const fake = createFakeSource({
      chats: [{ id: A, title: 'long', updatedAt: null }],
      turns: { [A]: [] },
      settings: { ...createFakeSource().data.settings, contextMode },
    })
    const source = fake.source
    source.fetchTurns.mockImplementation(async (_chatId: string, query: { afterSeq?: number; beforeSeq?: number; limit?: number }) => {
      const list = query.beforeSeq != null
        ? ALL.filter((t) => t.seq < query.beforeSeq!).slice(-(query.limit ?? 30))
        : query.afterSeq != null
          ? ALL.filter((t) => t.seq > query.afterSeq!)
          : ALL.slice(-(query.limit ?? 50))
      const oldest = list[0]?.seq ?? 1
      return { turns: list.map((t) => ({ ...t })), participants: [], myReadSeq: null, headSeq: HEAD, nextAfterSeq: null, prevBeforeSeq: oldest > 1 ? oldest : null }
    })
    source.fetchChatDocSettings = vi.fn(async () => checkpoint ?? { providerId: null, checkpointSeq: null, checkpointKind: null })
    source.saveChatDocSettings = vi.fn(async () => checkpoint!)
    const session = createConversationSession({ source, sleep: async () => undefined })
    await session.setScope(SCOPE)
    await flushPromises()
    const olderReads = () => source.fetchTurns.mock.calls.map((c: unknown[]) => (c[1] as { beforeSeq?: number }).beforeSeq).filter((s: unknown) => s != null)
    return { session, source, olderReads }
  }

  it('opening a chat whose checkpoint is older than the first page reads older pages until it is on screen', async () => {
    const { session, olderReads } = await setupLong('checkpoint', { providerId: null, checkpointSeq: 12, checkpointKind: 'include' })
    expect(session.state.turns[0].seq).toBe(11) // 41~70, then 11~40: #12 is loaded, no further page
    expect(olderReads()).toEqual([71, 41])
    expect(session.state.hasMoreBefore).toBe(true) // #1~#10 stay behind [이전 대화]
  })

  it('a checkpoint already on screen, none at all, or another context setting reads nothing more', async () => {
    for (const [mode, checkpoint] of [
      ['checkpoint', { providerId: null, checkpointSeq: 90, checkpointKind: 'exclude' }],
      ['checkpoint', null],
      ['recent', { providerId: null, checkpointSeq: 12, checkpointKind: 'include' }],
    ] as const) {
      const { session, olderReads } = await setupLong(mode, checkpoint)
      expect(session.state.turns[0].seq).toBe(71)
      expect(olderReads()).toEqual([])
    }
  })

  it('switching the context setting to 체크포인트 사용 brings the stored checkpoint message on screen', async () => {
    const { session, olderReads } = await setupLong('recent', { providerId: null, checkpointSeq: 12, checkpointKind: 'exclude' })
    expect(session.state.turns[0].seq).toBe(71)
    expect(await session.saveSettings({ context_mode: 'checkpoint' })).toBe(true)
    await flushPromises()
    expect(session.state.turns[0].seq).toBe(11)
    expect(olderReads()).toEqual([71, 41])
  })

  it('a failed older read stops there (the [이전 대화] button stays)', async () => {
    const { session, source, olderReads } = await setupLong('recent', { providerId: null, checkpointSeq: 12, checkpointKind: 'include' })
    source.fetchTurns.mockRejectedValue(new Error('offline'))
    await session.saveSettings({ context_mode: 'checkpoint' })
    await flushPromises()
    expect(olderReads()).toEqual([71]) // one try, no loop
    expect(session.state.turns[0].seq).toBe(71)
    expect(session.state.hasMoreBefore).toBe(true)
  })
})
