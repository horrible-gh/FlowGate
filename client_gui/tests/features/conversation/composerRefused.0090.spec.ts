import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { setCurrentUser } from '@/core/auth/currentUser'
import { grantProject, resetCapabilities } from '../../helpers/capabilities'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0090 (rework): the chat input is a card edit entry point. Posting, editing and deleting a message are the
// server's `project.document.update` (documents/routers/conversation_turns.py), so a person that answer refuses sees the
// input, send and the message menus off, with the server's sentence, and a stale click sends nothing.
registerAppMessages(i18n)

const A = 'p.m.0001.0002-CH'
let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  setCurrentUser({ username: 'tester', email: null, isAdmin: false })
  resetCapabilities()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  resetCapabilities()
})

const VIEWER = { 'project.document.update': { reason: 'role_limit', minimum_role: 'role_developer' } }

async function mountPanel() {
  const fake = createFakeSource({
    chats: [{ id: A, title: '조사', updatedAt: new Date().toISOString() }],
    turns: { [A]: [turn(1, 'user', '조사해 줘', null, { participantKey: 'u:me', messageVersion: 1 }), turn(2, 'ai', '확인했습니다')] },
  })
  fake.source.listProviders.mockResolvedValue({ providers: [{ id: 'opus', name: 'Opus' }], defaultProviderId: 'opus' })
  // The page names which participant is the signed-in person: only their own messages have the edit / delete menu.
  const original = fake.source.fetchTurns.getMockImplementation()!
  fake.source.fetchTurns.mockImplementation(async (chatId: string, query: never) => ({ ...(await original(chatId, query)), myParticipantKey: 'u:me' }))
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return fake
}
const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

describe('chat input vs project.document.update', () => {
  it('a viewer: input and send are off, one line gives the server\'s reason, nothing is sent', async () => {
    grantProject('p', 'role_viewer', VIEWER)
    const fake = await mountPanel()
    const input = find('conv-input')
    expect((input.element as HTMLTextAreaElement).disabled).toBe(true)
    expect((find('conv-send').element as HTMLButtonElement).disabled).toBe(true)
    const note = find('conv-permission-note')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('보기 전용')
    expect(note.text()).toContain('개발자')
    // A stale click on send reaches nothing.
    await find('conv-send').trigger('click')
    await flushPromises()
    expect(fake.source.postTurn).not.toHaveBeenCalled()
    expect(fake.source.startChatRun).not.toHaveBeenCalled()
  })

  it('the edit / delete menu of the person\'s own message is off for a viewer and on for a developer', async () => {
    grantProject('p', 'role_developer')
    await mountPanel()
    expect(find('tl-more').exists()).toBe(true)
    wrapper!.unmount()
    grantProject('p', 'role_viewer', VIEWER)
    await mountPanel()
    expect(find('tl-more').exists()).toBe(false)
  })

  it('a developer: input and send work, a message is posted, and there is no note', async () => {
    grantProject('p', 'role_developer')
    const fake = await mountPanel()
    expect((find('conv-input').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(find('conv-permission-note').exists()).toBe(false)
    await find('conv-input').setValue('안녕')
    expect((find('conv-send').element as HTMLButtonElement).disabled).toBe(false)
    await find('conv-send').trigger('click')
    await flushPromises()
    expect(fake.source.postTurn).toHaveBeenCalledTimes(1)
  })

  it('no answer yet changes nothing; a later "no" turns the input off and a later "yes" turns it on', async () => {
    await mountPanel()
    expect((find('conv-input').element as HTMLTextAreaElement).disabled).toBe(false)
    grantProject('p', 'role_viewer', VIEWER)
    await flushPromises()
    expect((find('conv-input').element as HTMLTextAreaElement).disabled).toBe(true)
    grantProject('p', 'role_developer')
    await flushPromises()
    expect((find('conv-input').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(find('conv-permission-note').exists()).toBe(false)
  })
})
