import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import { draftUserId } from '@/features/conversation/drafts'
import { toQuickMessage, type QuickMessage } from '@/features/conversation/quickMessages/api'
import {
  buildOutgoingText,
  loadStickyIds,
  stickyStorageKey,
  useQuickMessages,
} from '@/features/conversation/quickMessages/useQuickMessages'

const net = vi.hoisted(() => ({ list: vi.fn(), canEdit: vi.fn() }))
vi.mock('@/features/conversation/quickMessages/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/quickMessages/api')>()),
  fetchChatQuickMessages: net.list,
  fetchCanEditQuickMessages: net.canEdit,
}))

registerAppMessages(i18n)

// flowgate.v02test.0072 card 2: the quick message row above the chat input. Off = grey; on = red (계속, sticky) or
// blue (1회, once); a send carries the typed text + the on quick messages in row order; sticky survives F5 (browser
// storage per user and project); once turns off after an accepted send; a failed load hides the row and adds nothing.
const PROJECT = 'p'
const FLOW = 'p.m.0001'
const CHAT = 'p.m.0001.0002-CH'

const qm = (id: number, title: string, message: string, kind: 'sticky' | 'once', sortOrder = id): QuickMessage =>
  ({ id, title, message, kind, sortOrder })
const LIST = [qm(1, '근거', '근거를 붙여 주세요.', 'sticky'), qm(2, '짧게', '짧게 답해 주세요.', 'once'), qm(3, '표로', '표로 정리해 주세요.', 'once')]

let wrappers: VueWrapper[] = []
function mountComposer(props: Record<string, unknown> = {}) {
  const wrapper = mount(ConversationComposer, {
    props: {
      chatId: CHAT,
      projectId: PROJECT,
      groupId: FLOW,
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
  wrappers.push(wrapper)
  return wrapper
}

const buttons = (w: VueWrapper) => w.findAll('[data-test="conv-quick-btn"]')
const send = (w: VueWrapper) => w.get('[data-test="conv-send"]')
const type = async (w: VueWrapper, text: string) => { await w.get('[data-test="conv-input"]').setValue(text) }

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  net.list.mockReset().mockResolvedValue(LIST)
  net.canEdit.mockReset().mockResolvedValue(false)
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
})

describe('outgoing text and list rows', () => {
  it('appends each quick message on its own line after the typed text, and alone when nothing is typed', () => {
    expect(buildOutgoingText('안녕', ['A', 'B'])).toBe('안녕\nA\nB')
    expect(buildOutgoingText('   ', ['A'])).toBe('A')
    expect(buildOutgoingText('안녕', [])).toBe('안녕')
  })

  it('normalizes a server row and drops rows without an id or text', () => {
    expect(toQuickMessage({ id: 4, title: '제목', message: ' 문구 ', kind: 'sticky', sort_order: 2 }))
      .toEqual({ id: 4, title: '제목', message: '문구', kind: 'sticky', sortOrder: 2 })
    expect(toQuickMessage({ id: 5, title: '', message: '아주 긴 문구가 여기에 들어갑니다', kind: 'weird' }))
      .toEqual({ id: 5, title: '아주 긴 문구가 여기에', message: '아주 긴 문구가 여기에 들어갑니다', kind: 'once', sortOrder: 0 })
    expect(toQuickMessage({ id: 'x', message: 'a' })).toBeNull()
    expect(toQuickMessage({ id: 6, message: '  ' })).toBeNull()
  })
})

describe('useQuickMessages', () => {
  const make = (fetch: (projectId: string) => Promise<QuickMessage[]> = async () => LIST) =>
    useQuickMessages({ projectId: () => PROJECT, userId: () => 'u1', fetch })

  it('toggles sticky and once buttons; only on buttons are active, in row order', async () => {
    const q = make()
    await q.load()
    expect(q.visible.value).toBe(true)
    q.toggle(3)
    q.toggle(1)
    expect(q.active.value.map((i) => i.id)).toEqual([1, 3])
    q.toggle(3)
    expect(q.active.value.map((i) => i.id)).toEqual([1])
  })

  it('keeps sticky buttons on across a reload (F5) per user and project; once buttons start off', async () => {
    const q = make()
    await q.load()
    q.toggle(1)
    q.toggle(2)
    expect(loadStickyIds('u1', PROJECT)).toEqual([1])
    const again = make()
    await again.load()
    expect(again.active.value.map((i) => i.id)).toEqual([1])
    const other = useQuickMessages({ projectId: () => PROJECT, userId: () => 'u2', fetch: async () => LIST })
    await other.load()
    expect(other.active.value).toEqual([])
  })

  it('consume turns off only the once buttons that were sent', async () => {
    const q = make()
    await q.load()
    q.toggle(1)
    q.toggle(2)
    const sent = q.active.value
    q.toggle(3)
    q.consume(sent)
    expect(q.active.value.map((i) => i.id)).toEqual([1, 3])
  })

  it('a failed load hides the row, adds nothing and leaves the stored sticky state alone', async () => {
    localStorage.setItem(stickyStorageKey('u1', PROJECT), JSON.stringify([1]))
    const q = make(vi.fn(async () => { throw Object.assign(new Error('forbidden'), { response: { status: 403 } }) }))
    await q.load()
    expect(q.status.value).toBe('hidden')
    expect(q.active.value).toEqual([])
    expect(q.toggleAt(0)).toBe(false)
    expect(loadStickyIds('u1', PROJECT)).toEqual([1])
    const later = make()
    await later.load()
    expect(later.active.value.map((i) => i.id)).toEqual([1])
  })
})

describe('the row in the composer', () => {
  it('shows grey buttons (no kind shown) and turns them red (계속) / blue (1회) when pressed, with the kind on hover', async () => {
    const w = mountComposer()
    await flushPromises()
    expect(net.list).toHaveBeenCalledWith(PROJECT)
    const row = buttons(w)
    expect(row.map((b) => b.text())).toEqual(['근거', '짧게', '표로'])
    for (const b of row) {
      expect(b.attributes('data-state')).toBe('off')
      expect(b.classes()).not.toContain('is-on-sticky')
      expect(b.classes()).not.toContain('is-on-once')
    }
    expect(row[0].attributes('title')).toBe('[빨강 · 계속] 근거를 붙여 주세요.')
    expect(row[1].attributes('title')).toBe('[파랑 · 1회] 짧게 답해 주세요.')
    await row[0].trigger('click')
    await row[1].trigger('click')
    expect(buttons(w)[0].classes()).toContain('is-on-sticky')
    expect(buttons(w)[1].classes()).toContain('is-on-once')
    expect(buttons(w)[0].attributes('aria-pressed')).toBe('true')
  })

  it('sends the message + the on quick messages in row order; once turns off, sticky stays on', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await flushPromises()
    await buttons(w)[2].trigger('click')
    await buttons(w)[0].trigger('click')
    await type(w, '요약해 줘')
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).toHaveBeenCalledTimes(1)
    expect(onSend.mock.calls[0][0]).toBe('요약해 줘\n근거를 붙여 주세요.\n표로 정리해 주세요.')
    expect((w.get('[data-test="conv-input"]').element as HTMLTextAreaElement).value).toBe('')
    expect(buttons(w)[0].attributes('data-state')).toBe('on')
    expect(buttons(w)[2].attributes('data-state')).toBe('off')
  })

  it('an empty input can be sent when a quick message is on', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await flushPromises()
    expect(send(w).attributes('disabled')).toBeDefined()
    await buttons(w)[1].trigger('click')
    expect(send(w).attributes('disabled')).toBeUndefined()
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(onSend.mock.calls[0][0]).toBe('짧게 답해 주세요.')
    expect(buttons(w)[1].attributes('data-state')).toBe('off')
  })

  it('a send that fails keeps the once button on and the typed text in the input', async () => {
    const onSend = vi.fn(async () => false)
    const w = mountComposer({ onSend })
    await flushPromises()
    await buttons(w)[1].trigger('click')
    await type(w, '질문')
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend.mock.calls[0][0]).toBe('질문\n짧게 답해 주세요.')
    expect(buttons(w)[1].attributes('data-state')).toBe('on')
    expect((w.get('[data-test="conv-input"]').element as HTMLTextAreaElement).value).toBe('질문')
  })

  it('keeps a sticky button on after a remount (F5)', async () => {
    const first = mountComposer()
    await flushPromises()
    await buttons(first)[0].trigger('click')
    first.unmount()
    wrappers = wrappers.filter((x) => x !== first)
    const again = mountComposer()
    await flushPromises()
    expect(buttons(again)[0].attributes('data-state')).toBe('on')
    expect(loadStickyIds(draftUserId(), PROJECT)).toEqual([1])
  })

  it('Alt+1..9 in the input toggles the 1st..9th button', async () => {
    const w = mountComposer()
    await flushPromises()
    const input = w.get('[data-test="conv-input"]')
    await input.trigger('keydown', { key: '2', code: 'Digit2', altKey: true })
    expect(buttons(w)[1].attributes('data-state')).toBe('on')
    await input.trigger('keydown', { key: '2', code: 'Digit2', altKey: true })
    expect(buttons(w)[1].attributes('data-state')).toBe('off')
    await input.trigger('keydown', { key: '9', code: 'Digit9', altKey: true })
    expect(buttons(w).every((b) => b.attributes('data-state') === 'off')).toBe(true)
  })

  it('a failed load (or no read permission) hides the row and sends the text alone, keeping the stored sticky state', async () => {
    localStorage.setItem(stickyStorageKey(draftUserId(), PROJECT), JSON.stringify([1]))
    net.list.mockRejectedValue(Object.assign(new Error('forbidden'), { response: { status: 403 } }))
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await flushPromises()
    expect(w.find('[data-test="conv-quick-row"]').exists()).toBe(false)
    await type(w, '그냥 메시지')
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend.mock.calls[0][0]).toBe('그냥 메시지')
    expect(loadStickyIds(draftUserId(), PROJECT)).toEqual([1])
  })

  it('shows "등록된 퀵메시지가 없습니다" when the project has none', async () => {
    net.list.mockResolvedValue([])
    const w = mountComposer()
    await flushPromises()
    expect(w.get('[data-test="conv-quick-row"]').exists()).toBe(true)
    expect(w.get('[data-test="conv-quick-empty"]').text()).toBe('등록된 퀵메시지가 없습니다')
  })
})
