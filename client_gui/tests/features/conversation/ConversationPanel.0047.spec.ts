import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0047 — the Conversation panel on the v9 layout (R0001 §2.3) and the chat that
// starts from the composer (§2.1, §2.2).
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const A = 'p.m.0001.0002-CH'
const NEW = 'p.m.0001.0010-CH'
const LONG = 'Claude Opus 5 · project default provider with a very long name'

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

async function mountPanel(init: Parameters<typeof createFakeSource>[0] = {}, props: Record<string, unknown> = {}) {
  const fake = createFakeSource(init)
  fake.source.listProviders.mockResolvedValue({ providers: [{ id: 'opus', name: LONG }, { id: 'sonnet', name: 'Sonnet 5' }], defaultProviderId: 'opus' })
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return { ...fake, wrapper }
}

const withChat = () => ({
  chats: [{ id: A, title: '인증 오류 조사', updatedAt: new Date().toISOString() }],
  turns: { [A]: [turn(1, 'user', '조사해 줘'), turn(2, 'ai', '확인했습니다')] },
  participants: [
    { key: 'u:test', kind: 'user' as const, displayName: 'test', lastReadSeq: 42 },
    { key: 'ai:opus', kind: 'ai' as const, displayName: 'Opus', lastReadSeq: 41 },
  ],
})

const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
const findAll = (sel: string) => wrapper!.findAll(`[data-test="${sel}"]`)
const order = (root: Element, selectors: string[]) =>
  Array.from(root.querySelectorAll(selectors.map((s) => `[data-test="${s}"]`).join(','))).map((n) => n.getAttribute('data-test'))

describe('v9 layout (R0001 §2.3)', () => {
  it('stacks header, participant chips, timeline and composer in that order', async () => {
    await mountPanel(withChat())
    const panel = find('conversation-panel').element
    expect(order(panel, ['conv-head', 'conv-participants', 'conv-log', 'conv-composer'])).toEqual([
      'conv-head',
      'conv-participants',
      'conv-log',
      'conv-composer',
    ])
    // The chips keep their own style: 참여  ● test #42  ◆ Opus #41 — not a folded count.
    expect(find('conv-participants').text().replace(/\s+/g, '')).toBe('참여●test#42◆Opus#41')
  })

  it('puts input and send on the first composer line and the options on the second', async () => {
    await mountPanel(withChat())
    const composer = find('conv-composer').element
    expect(order(composer, ['conv-input', 'conv-send', 'conv-provider-btn', 'conv-source-mode', 'conv-settings-btn', 'conv-more-btn'])).toEqual([
      'conv-input',
      'conv-send',
      'conv-provider-btn',
      'conv-source-mode',
      'conv-settings-btn',
      'conv-more-btn',
    ])
    // Mention copy and AI call are no standing buttons any more, and there is no <select> AI list.
    expect(find('conv-copy-mention').exists()).toBe(false)
    expect(find('conv-invoke-ai').exists()).toBe(false)
    expect(find('conv-composer').find('select').exists()).toBe(false)
  })

  it('picks the AI from a compact popover; a long name stays on one clipped line', async () => {
    const { source } = await mountPanel(withChat())
    const btn = find('conv-provider-btn')
    expect(find('conv-provider-name').text()).toBe(LONG)
    expect(find('conv-provider-name').classes()).toContain('cmp-model-name')
    expect(btn.attributes('title')).toBe(LONG)
    expect(btn.attributes('aria-haspopup')).toBe('listbox')
    expect(btn.attributes('aria-expanded')).toBe('false')
    await btn.trigger('click')
    expect(btn.attributes('aria-expanded')).toBe('true')
    const options = findAll('conv-provider-option')
    expect(options.map((o) => o.attributes('aria-selected'))).toEqual(['true', 'false'])
    await options[1].trigger('click')
    expect(find('conv-provider-pop').exists()).toBe(false)
    expect(find('conv-provider-name').text()).toBe('Sonnet 5')
    await find('conv-more-btn').trigger('click')
    await find('conv-invoke-ai').trigger('click')
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'sonnet')
  })

  it('closes a popover on Esc and on a press outside', async () => {
    await mountPanel(withChat())
    await find('conv-provider-btn').trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(find('conv-provider-pop').exists()).toBe(false)
    await find('conv-more-btn').trigger('click')
    expect(find('conv-more-menu').exists()).toBe(true)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(find('conv-more-menu').exists()).toBe(false)
  })

  it('shows the source modes as named icons with the pressed one marked', async () => {
    await mountPanel(withChat())
    const buttons = find('conv-source-mode').findAll('button')
    expect(buttons.map((b) => b.attributes('aria-label'))).toEqual(['읽기', '편집', '1회 편집'])
    expect(buttons.map((b) => b.attributes('title'))).toEqual([
      'AI가 소스를 읽기만 합니다',
      'AI가 소스를 수정할 수 있습니다',
      '다음 AI 호출 한 번만 소스를 수정할 수 있습니다',
    ])
    expect(buttons.map((b) => b.attributes('aria-pressed'))).toEqual(['true', 'false', 'false'])
    expect(buttons[0].find('svg').exists()).toBe(true)
  })

  it('keeps mention copy and AI call in the ⋯ menu', async () => {
    const { source } = await mountPanel(withChat())
    const more = find('conv-more-btn')
    expect(more.attributes('aria-label')).toBe('추가 메뉴')
    await more.trigger('click')
    expect(find('conv-more-menu').findAll('[role="menuitem"]').map((i) => i.text())).toEqual(['⧉멘션 복사', '◆AI 호출'])
    await find('conv-copy-mention').trigger('click')
    await flushPromises()
    expect(source.issueChatMention).toHaveBeenCalledWith(A)
    expect(find('conv-more-menu').exists()).toBe(false)
  })
})

describe('starting a chat from the composer (R0001 §2.1, §2.2)', () => {
  it('a Flow without chats takes the first message at once and the AI titles the chat', async () => {
    const { source, data } = await mountPanel()
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('새 대화')
    expect(find('conv-log-empty').text()).toBe('아직 메시지가 없습니다. 아래에서 대화를 시작하세요.')
    const input = find('conv-input')
    expect(input.attributes('disabled')).toBeUndefined()
    await input.setValue('로그인 오류 원인')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledTimes(1)
    expect(data.turns[NEW].map((t) => t.body)).toEqual(['로그인 오류 원인'])
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['로그인 오류 원인'])
    expect(source.createChat).toHaveBeenCalledWith({ projectId: 'p', groupId: 'p.m.0001' }, '새 대화', { autoTitle: true })
    expect(source.autoTitleChat).toHaveBeenCalledWith(NEW, 'opus')
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('AI: 로그인 오류 원인')
    await find('conv-list-btn').trigger('click')
    expect(findAll('conv-list-item').map((i) => i.find('.conv-list-name').text())).toEqual(['AI: 로그인 오류 원인'])
  })

  it('a title typed on the draft names the chat and is not replaced', async () => {
    const { source } = await mountPanel()
    const title = find('conv-title-input')
    ;(title.element as HTMLInputElement).focus()
    await title.setValue('빌드 정리')
    await title.trigger('blur')
    await find('conv-input').setValue('로그 봐 줘')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect(source.renameChat).not.toHaveBeenCalled()
    expect(source.createChat).toHaveBeenCalledWith({ projectId: 'p', groupId: 'p.m.0001' }, '빌드 정리')
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('빌드 정리')
  })

  it('an AI title that arrives during a title edit does not undo itself on blur', async () => {
    const { source } = await mountPanel()
    let answer!: () => void
    const original = source.autoTitleChat.getMockImplementation()!
    source.autoTitleChat.mockImplementationOnce((...args: [string, string]) => new Promise((resolve) => (answer = () => resolve(original(...args)))))
    await find('conv-input').setValue('hello')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    const title = find('conv-title-input')
    ;(title.element as HTMLInputElement).focus()
    await title.trigger('focus')
    answer()
    await flushPromises()
    ;(title.element as HTMLInputElement).blur()
    await title.trigger('blur')
    await flushPromises()
    expect(source.renameChat).not.toHaveBeenCalled()
    expect((title.element as HTMLInputElement).value).toBe('AI: hello')
  })

  it('a failed first send keeps the text, the chat and its retry', async () => {
    const { source } = await mountPanel()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'nope'))
    await find('conv-input').setValue('hello')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect((find('conv-input').element as HTMLTextAreaElement).value).toBe('hello')
    expect(localStorage.getItem(`flowgate.user.guest.chat.drafts.${NEW}`)).toBe('hello')
    expect(find('tl-retry').exists()).toBe(true)
    expect(source.autoTitleChat).not.toHaveBeenCalled()
    await find('tl-retry').trigger('click')
    await flushPromises()
    expect(source.autoTitleChat).toHaveBeenCalledTimes(1)
  })

  it('[+] from an open chat shows an empty draft and stores nothing', async () => {
    const { source } = await mountPanel(withChat())
    await find('conv-new-btn').trigger('click')
    await flushPromises()
    expect(findAll('tl-message')).toHaveLength(0)
    expect(find('conv-participants').exists()).toBe(false)
    expect(document.activeElement).toBe(find('conv-input').element)
    await find('conv-list-btn').trigger('click')
    await findAll('conv-list-item')[0].trigger('click')
    await flushPromises()
    expect(source.createChat).not.toHaveBeenCalled()
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('인증 오류 조사')
  })

  it('read-only mode cannot start a chat', async () => {
    await mountPanel({}, { readOnly: true })
    expect(find('conv-input').attributes('disabled')).toBeDefined()
    expect(find('conv-title-input').attributes('disabled')).toBeDefined()
  })
})
