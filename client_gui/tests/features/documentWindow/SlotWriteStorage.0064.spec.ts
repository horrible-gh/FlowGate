import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import SlotWriteWindow from '@/features/documentWindow/SlotWriteWindow.vue'
import { cardLineWrite } from '@/features/document'
import { closeSlotWriteWindow, openSlotWriteWindow, slotWriteWindowList } from '@/features/documentWindow/slotWriteWindows'

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
