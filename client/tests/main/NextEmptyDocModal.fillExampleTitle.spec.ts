import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NextEmptyDocModal from '@main/components/NextEmptyDocModal.vue'

const { postRequest } = vi.hoisted(() => ({
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({ postRequest }))

// group 0369 rejection rework: the fill button uses the localized label of the
// document type supplied to this next-document dialog.
describe('NextEmptyDocModal — document-type title fill button', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    postRequest.mockReset()
  })

  function mountModal() {
    return mount(NextEmptyDocModal, {
      props: {
        visible: true,
        projectId: 'flowgate',
        groupId: 'flowgate.default.0369',
        prevDocId: 'flowgate.default.0369.0001-R',
        docType: 'T',
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
  }

  it.each([
    ['en', 'Task Instruction', 'Fill title with document type'],
    ['ja', '作業指示', '文書種別をタイトルに入力'],
    ['ko', '작업지시', '제목에 문서 유형 채우기'],
  ] as const)('locale %s: fills the title with the T document label and matching tooltip', async (locale, expectedWord, expectedTooltip) => {
    i18n.global.locale.value = locale
    const wrapper = mountModal()
    await flushPromises()

    await wrapper.find('input.form-ctrl').setValue('some existing title')

    const fillBtnAttrs = wrapper.find('.title-fill-btn')
    expect(fillBtnAttrs.exists()).toBe(true)
    expect(fillBtnAttrs.attributes('type')).toBe('button')
    expect(fillBtnAttrs.attributes('aria-label')).toBe(expectedTooltip)
    expect(fillBtnAttrs.attributes('title')).toBe(expectedTooltip)

    // The teleport stub remounts the subtree on each reactive update, so the input
    // and button must be re-queried after every click instead of reusing stale refs.
    await wrapper.find('.title-fill-btn').trigger('click')
    expect((wrapper.find('input.form-ctrl').element as HTMLInputElement).value).toBe(expectedWord)

    // Repeated clicks are idempotent — same document label, no locale cycling.
    await wrapper.find('.title-fill-btn').trigger('click')
    expect((wrapper.find('input.form-ctrl').element as HTMLInputElement).value).toBe(expectedWord)
    expect(i18n.global.locale.value).toBe(locale)
  })
})
