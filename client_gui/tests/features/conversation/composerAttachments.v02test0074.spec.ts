import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS, toAttachmentActivity, toAttachmentGroups } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import { ConversationPanel } from '@/features/conversation'
import { createFakeSource, turn } from './fakeConversationSource'

// v02test.0074 card 1: the 📎 button of the chat input (right of the AI picker and the source mode icons). It asks
// the container to open / close the Flow's attachment window and shows the waiting AI upload requests as a yellow badge.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function mountComposer(props: Record<string, unknown> = {}) {
  wrapper = mount(ConversationComposer, {
    props: {
      chatId: 'p.m.0001.0002-CH',
      disabled: false,
      settings: DEFAULT_CHAT_SETTINGS,
      savingSettings: false,
      pendingSourceMode: null,
      providers: [],
      selectedProviderId: '',
      providersStatus: 'ready',
      hasProviders: false,
      sending: false,
      running: false,
      runStarting: false,
      cancelling: false,
      copyingMention: false,
      manualCopyText: null,
      onSend: vi.fn(async () => true),
      ...props,
    },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true } },
  })
  return wrapper
}
const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

describe('📎 button of the chat input', () => {
  it('sits right of the AI picker and the source mode icons, before the settings side', () => {
    mountComposer()
    const button = find('conv-attachments').element
    const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(wrapper!.findComponent({ name: 'ComposerModelPicker' }).element, button)).toBe(true)
    expect(follows(wrapper!.findComponent({ name: 'ComposerSourceModes' }).element, button)).toBe(true)
    expect(follows(button, wrapper!.get('.conv-assist-spacer').element)).toBe(true)
    expect(find('conv-attachments').text()).toContain('📎')
  })

  it('is the shared Button (ghost) and has an accessible name', () => {
    mountComposer()
    const button = find('conv-attachments')
    expect(button.classes()).toEqual(expect.arrayContaining(['ui-btn', 'ui-btn--ghost']))
    expect(button.attributes('type')).toBe('button')
    expect(button.attributes('aria-label')).toBe('첨부파일')
  })

  it('asks the container to toggle the window on every press', async () => {
    mountComposer()
    await find('conv-attachments').trigger('click')
    await find('conv-attachments').trigger('click')
    expect(wrapper!.emitted('toggle-attachments')).toHaveLength(2)
  })

  it('does not send the form when pressed', async () => {
    const onSend = vi.fn(async () => true)
    mountComposer({ onSend })
    await find('conv-attachments').trigger('click')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('shows pressed while the window is open', async () => {
    mountComposer({ attachmentsOpen: false })
    expect(find('conv-attachments').attributes('aria-pressed')).toBe('false')
    await wrapper!.setProps({ attachmentsOpen: true })
    expect(find('conv-attachments').attributes('aria-pressed')).toBe('true')
    expect(find('conv-attachments').classes()).toContain('is-open')
  })

  it('shows the waiting count as a badge, and none at 0', async () => {
    mountComposer()
    expect(find('conv-attachments-badge').exists()).toBe(false)
    await wrapper!.setProps({ attachmentsPending: 3 })
    expect(find('conv-attachments-badge').text()).toBe('3')
    expect(find('conv-attachments').attributes('aria-label')).toBe('첨부파일 · 승인 대기 3건')
    await wrapper!.setProps({ attachmentsPending: 0 })
    expect(find('conv-attachments-badge').exists()).toBe(false)
  })

  it('uses theme tokens only: no fixed colour in its rules', () => {
    const style = readFileSync(resolve(__dirname, '../../../src/features/conversation/ConversationComposer.vue'), 'utf8').split('<style')[1] ?? ''
    for (const selector of ['.conv-attach', '.conv-attach.is-open', '.conv-attach-badge']) {
      const at = style.indexOf(`${selector} {`)
      expect(at, selector).toBeGreaterThanOrEqual(0)
      const rule = style.slice(at, style.indexOf('}', at))
      expect(rule, selector).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/)
    }
    const badge = style.slice(style.indexOf('.conv-attach-badge {'))
    expect(badge).toContain('var(--fg2-status-warning-bg)')
  })
})

describe('panel: the badge count and the toggle', () => {
  const CHAT = 'p.m.0001.0002-CH'
  const OTHER = 'p.m.0001.0003-CH'
  const row = (id: string, status: string) => ({ request_id: id, status, filename: `${id}.md`, size: 1, ai_run_id: 'run-1' })
  const activityOf = (rows: ReturnType<typeof row>[]) => {
    const [group] = toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', anchor_seq: 2, anchor_position: 'after', anchor_state: 'reply', pending: 0, requests: rows }])
    return toAttachmentActivity(group)
  }

  // two chats of the same Flow: the open one holds `rows`, the other one holds `otherRows`.
  async function mountPanel(rows: ReturnType<typeof row>[], props: Record<string, unknown> = {}, otherRows: ReturnType<typeof row>[] = []) {
    const fake = createFakeSource({
      chats: [
        { id: CHAT, title: '첨부', updatedAt: new Date().toISOString() },
        { id: OTHER, title: '다른 대화', updatedAt: '2026-10-06T07:00:00Z' },
      ],
      turns: {
        [CHAT]: [turn(1, 'user', '파일 줘', '2026-10-06T08:02:00Z'), turn(2, 'ai', '드렸습니다', '2026-10-06T08:03:00Z')],
        [OTHER]: [turn(1, 'user', '다른 일', '2026-10-06T07:02:00Z'), turn(2, 'ai', '네', '2026-10-06T07:03:00Z')],
      },
      activities: { [CHAT]: [activityOf(rows)], [OTHER]: [activityOf(otherRows)] },
    })
    wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, ...props },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
    return wrapper
  }
  const composerProp = () => wrapper!.findComponent({ name: 'ConversationComposer' }).props('attachmentsPending')

  it('shows the Flow-level count the container hands in', async () => {
    await mountPanel([], { attachmentsPending: 4 })
    expect(composerProp()).toBe(4)
    expect(find('conv-attachments-badge').text()).toBe('4')
    expect(find('conv-attachments').attributes('aria-label')).toBe('첨부파일 · 승인 대기 4건')
  })

  it('shows no badge when nothing waits in the Flow', async () => {
    await mountPanel([row('c', 'approved')])
    expect(composerProp()).toBe(0)
    expect(find('conv-attachments-badge').exists()).toBe(false)
  })

  it('does not count the open chat by itself: waiting rows of this chat are not the Flow count', async () => {
    await mountPanel([row('a', 'pending'), row('b', 'approving')], { attachmentsPending: 0 })
    expect(find('conv-attachments-badge').exists()).toBe(false)
    await wrapper!.setProps({ attachmentsPending: 7 })
    expect(find('conv-attachments-badge').text()).toBe('7')
  })

  it('keeps the Flow count when another chat of the Flow is opened (regression: it was counted per open chat)', async () => {
    // the open chat has nothing waiting; the other chat and a card hold the Flow's 3 waiting requests.
    await mountPanel([row('c', 'approved')], { attachmentsPending: 3 }, [row('x', 'pending')])
    expect(find('conv-attachments-badge').text()).toBe('3')
    wrapper!.findComponent({ name: 'ConversationHeader' }).vm.$emit('select', OTHER)
    await flushPromises()
    expect(find('conv-attachments-badge').text()).toBe('3')
    wrapper!.findComponent({ name: 'ConversationHeader' }).vm.$emit('select', CHAT)
    await flushPromises()
    expect(find('conv-attachments-badge').text()).toBe('3')
  })

  it('hands the open state down and passes the toggle up to the container', async () => {
    await mountPanel([], { attachmentsOpen: true })
    expect(wrapper!.findComponent({ name: 'ConversationComposer' }).props('attachmentsOpen')).toBe(true)
    await find('conv-attachments').trigger('click')
    expect(wrapper!.emitted('toggle-attachments')).toHaveLength(1)
  })
})
