import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import SlotWriteWindow from '@/features/documentWindow/SlotWriteWindow.vue'
import { cardLineWrite } from '@/features/document'
import { closeSlotWriteWindow, openSlotWriteWindow, slotWriteWindowList } from '@/features/documentWindow/slotWriteWindows'
import researchMessages from '@/features/designer/inspector/features/researchCard/messages'

vi.mock('@/features/document', async (original) => ({
  ...await original<typeof import('@/features/document')>(),
  cardLineWrite: vi.fn(),
}))
registerAppMessages(i18n)

describe('card line storage refusal (0064)', () => {
  it.each(['ko', 'en', 'ja'] as const)('keeps the body and permits retry after a 503 (%s)', async (locale) => {
    i18n.global.locale.value = locale
    const written = vi.fn()
    const reread = vi.fn()
    openSlotWriteWindow({ flowId: 'f1', nodeKey: 'n1', slot: 'instruction', docType: 'N', name: '지시서', cardTitle: '조사', nodeRunning: false, written, reread })
    const win = slotWriteWindowList()[0]
    const wrapper = mount(SlotWriteWindow, { props: { win }, global: {
      plugins: [i18n], stubs: { ModelessShell: { template: '<div><slot name="header"/><slot/></div>' } },
    } })
    try {
      vi.mocked(cardLineWrite).mockRejectedValueOnce({ response: { status: 503, data: {
        error: { code: 'document_storage_unwritable', message: 'storage failed', params: {} },
      } } })
      const editor = wrapper.get<HTMLTextAreaElement>('[data-test="slot-write-textarea"]')
      await editor.setValue('본문\nKeep this text')
      await wrapper.get('[data-test="slot-write-save"]').trigger('click')
      await flushPromises()
      expect(editor.element.value).toBe('본문\nKeep this text')
      expect(wrapper.get('[data-test="slot-write-notice"]').attributes('data-key')).toBe('document_storage_unwritable')
      expect(wrapper.text()).toContain(i18n.global.t('gui.document_window.write.failure.document_storage_unwritable'))
      expect(slotWriteWindowList()).toHaveLength(1)
      expect(written).not.toHaveBeenCalled()
      expect(reread).not.toHaveBeenCalled()
      vi.mocked(cardLineWrite).mockResolvedValueOnce({ flowId: 'f1', nodeKey: 'n1', slot: 'instruction', docType: 'N', docId: 'd1', boundAsResult: false, line: null })
      await wrapper.get('[data-test="slot-write-save"]').trigger('click')
      await flushPromises()
      expect(cardLineWrite).toHaveBeenLastCalledWith('f1', 'n1', 'instruction', '본문\nKeep this text')
      expect(written).toHaveBeenCalledOnce()
      expect(slotWriteWindowList()).toHaveLength(0)
    } finally {
      wrapper.unmount()
      closeSlotWriteWindow(win.key)
      vi.clearAllMocks()
    }
  })
})

// flowgate.v02.0088 F14 (D0004 §3.2): the [작성] window shows the line's kind from its nameKey in the current language,
// so a language switch while it stays open changes only the kind; a request with only `name` (an older caller) or with
// a key the catalog does not have shows `name` as it came. Reopening the same slot is the same window, whatever the key.
describe('the kind name of the [작성] window (0088 F14)', () => {
  const KEY = 'gui.inspector.researchCard.slot.instruction'
  for (const locale of ['ko', 'en', 'ja'] as const) {
    i18n.global.mergeLocaleMessage(locale, { gui: { inspector: { researchCard: researchMessages[locale] } } })
  }
  const stub = { ModelessShell: { template: '<div><slot name="header"/><slot/></div>' } }
  const kind = (wrapper: ReturnType<typeof mount>) => wrapper.get('.slot-write-kind').text()
  const request = (extra: Record<string, unknown>) => ({
    flowId: 'f88', nodeKey: 'n1', slot: 'instruction', docType: 'N', name: '지시서', cardTitle: '조사 카드 제목', nodeRunning: false, ...extra,
  })

  it('translates the kind on a language switch and keeps the typed text and the card title', async () => {
    i18n.global.locale.value = 'ko'
    openSlotWriteWindow(request({ nameKey: KEY }))
    const win = slotWriteWindowList()[0]
    const wrapper = mount(SlotWriteWindow, { props: { win }, global: { plugins: [i18n], stubs: stub } })
    try {
      expect(kind(wrapper)).toContain('지시서')
      const editor = wrapper.get<HTMLTextAreaElement>('[data-test="slot-write-textarea"]')
      await editor.setValue('작성 중인 본문')
      i18n.global.locale.value = 'ja'
      await flushPromises()
      expect(kind(wrapper)).toContain('指示書')
      expect(kind(wrapper)).not.toContain('지시서')
      i18n.global.locale.value = 'en'
      await flushPromises()
      expect(kind(wrapper)).toContain('Instruction')
      expect(editor.element.value).toBe('작성 중인 본문')
      expect(wrapper.get('[data-test="slot-write-card"]').text()).toBe('조사 카드 제목')
      expect(cardLineWrite).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
      closeSlotWriteWindow(win.key)
      i18n.global.locale.value = 'ko'
    }
  })

  it.each([
    ['no nameKey (an older caller)', {}],
    ['an unknown nameKey', { nameKey: 'gui.inspector.researchCard.slot.not_a_kind' }],
    ['a null nameKey', { nameKey: null }],
  ])('shows name as it came for %s, in any language', async (_label, extra) => {
    i18n.global.locale.value = 'ja'
    openSlotWriteWindow(request({ name: '사용자 종류', ...extra }))
    const win = slotWriteWindowList()[0]
    const wrapper = mount(SlotWriteWindow, { props: { win }, global: { plugins: [i18n], stubs: stub } })
    try {
      expect(kind(wrapper)).toContain('사용자 종류')
    } finally {
      wrapper.unmount()
      closeSlotWriteWindow(win.key)
      i18n.global.locale.value = 'ko'
    }
  })

  it('reopening the slot with or without a key is the same window, and the key is never written', async () => {
    openSlotWriteWindow(request({}))
    openSlotWriteWindow(request({ nameKey: KEY }))
    try {
      expect(slotWriteWindowList()).toHaveLength(1)
      expect(slotWriteWindowList()[0].key).toBe(['f88', 'n1', 'instruction'].join(String.fromCharCode(10)))
      expect(slotWriteWindowList()[0].request.nameKey).toBe(KEY)
    } finally {
      closeSlotWriteWindow(slotWriteWindowList()[0].key)
    }
  })
})
