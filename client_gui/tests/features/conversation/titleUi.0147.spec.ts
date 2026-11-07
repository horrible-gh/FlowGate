import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import type { ConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, turn } from './fakeConversationSource'
registerAppMessages(i18n)
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
let wrapper: VueWrapper | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null })
const header = () => (wrapper!.get('[data-test=conv-title-input]').element as HTMLInputElement).value
const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex')
function fixture() {
  return createFakeSource({ chats: [
    { id: A, title: 'New chat', updatedAt: null, autoTitle: { state: 'owed', provisionalTitle: 'New chat' } },
    { id: B, title: 'Other chat', updatedAt: null },
  ], turns: { [A]: [turn(1, 'user', 'synthetic title input', null, { sourceRunId: null }), turn(2, 'ai', 'saved reply', null, { sourceRunId: 'r1' })] },
  activities: { [A]: [{ kind: 'command', id: 'isolated-command', runId: 'r1', createdAt: null, command: 'fixture only', state: 'succeeded', exitCode: 0,
    anchor: { seq: 2, position: 'before', state: 'reply' } }] } })
}
async function open(source: ReturnType<typeof createFakeSource>['source']) {
  wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: 'p.m.0001', source }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises(); await flushPromises()
  return (wrapper.vm as unknown as { session: ConversationSession }).session
}
async function listTitle(id: string) {
  await wrapper!.get('[data-test=conv-list-btn]').trigger('click'); await flushPromises()
  const text = wrapper!.get(`[data-chat-id="${id}"] .conv-list-name`).text()
  await wrapper!.get('[data-test=conv-list-btn]').trigger('click')
  return text
}

describe('0147 actual Vue DOM with isolated source (not browser/server E2E)', () => {
  it('stored title, list, header and new-session remount agree without changing turns/commands', async () => {
    const { source, data } = fixture()
    const before = digest({ turns: data.turns, activities: data.activities })
    const session = await open(source)
    const title = data.chats[0].title
    expect(title).toBe('AI: synthetic title input')
    expect(header()).toBe(title)
    expect(await listTitle(A)).toBe(title)
    await session.selectChat(B); await session.selectChat(A); await flushPromises()
    expect(header()).toBe(title)
    wrapper!.unmount(); wrapper = null
    await open(source)
    expect(header()).toBe(title)
    expect(await listTitle(A)).toBe(title)
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
    expect(source.postTurn).not.toHaveBeenCalled()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(digest({ turns: data.turns, activities: data.activities })).toBe(before)
    console.log('0147_TITLE_DOM', JSON.stringify({ chatId: A, stored: title, state: data.chats[0].autoTitle?.state, header: header(), list: title, remount: true, preservedHash: before }))
  })

  it('late title updates its list row while another chat stays active', async () => {
    const { source, data } = fixture()
    let finish!: () => void
    const original = source.autoTitleChat.getMockImplementation()!
    source.autoTitleChat.mockImplementationOnce((...args: unknown[]) => new Promise((resolve) => { finish = () => resolve(original(...args)) }))
    const session = await open(source)
    await session.selectChat(B); await flushPromises()
    finish(); await flushPromises()
    expect(header()).toBe('Other chat')
    expect(await listTitle(A)).toBe('AI: synthetic title input')
    expect(session.state.activeChatId).toBe(B)
    console.log('0147_TITLE_DELAY_DOM', JSON.stringify({ active: B, header: header(), storedA: data.chats[0].title, listA: await listTitle(A) }))
  })

  it('manual rename after server success but before response remains in header/list', async () => {
    const { source, data } = fixture()
    let finish!: () => void
    const original = source.autoTitleChat.getMockImplementation()!
    source.autoTitleChat.mockImplementationOnce(async (...args: unknown[]) => {
      const saved = await original(...args)
      return new Promise((resolve) => { finish = () => resolve(saved) })
    })
    const session = await open(source)
    await session.renameActiveChat('Manual winner')
    await session.renameActiveChat('New chat')
    finish(); await flushPromises()
    expect(data.chats[0].autoTitle?.state).toBe('manual')
    expect(header()).toBe('New chat')
    expect(await listTitle(A)).toBe('New chat')
    console.log('0147_TITLE_MANUAL_DOM', JSON.stringify({ stored: data.chats[0].title, state: data.chats[0].autoTitle?.state, header: header() }))
  })
})
