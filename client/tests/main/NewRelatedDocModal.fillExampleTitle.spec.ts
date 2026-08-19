import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRelatedDocModal from '@main/components/NewRelatedDocModal.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({ getRequest, postRequest }))

// group 0369 rejection rework: the fill button follows the document type currently
// selected in the derived-document form and uses that type's localized label.
describe('NewRelatedDocModal — document-type title fill button', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('flowgate')
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/documents/detail')) {
        return Promise.resolve({
          data: {
            doc_id: 'flowgate.default.0369.0001-R',
            group_id: 'flowgate.default.0369',
            project_id: 'flowgate',
            module: 'default',
          },
        })
      }
      return Promise.resolve({ data: { data: { nodes: [] } } })
    })
  })

  function mountModal() {
    return mount(NewRelatedDocModal, {
      props: {
        tab: { id: 'flowgate.default.0369.0001-R', title: 'R0001', path: '', type: 'md' },
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
  }

  it.each([
    ['en', 'Design Instruction', 'Task Instruction', 'Fill title with document type'],
    ['ja', '設計指示', '作業指示', '文書種別をタイトルに入力'],
    ['ko', '설계지시', '작업지시', '제목에 문서 유형 채우기'],
  ] as const)('locale %s: follows the selected document type and matching tooltip', async (locale, expectedDefault, expectedTask, expectedTooltip) => {
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
    expect((wrapper.find('input.form-ctrl').element as HTMLInputElement).value).toBe(expectedDefault)

    // Changing the selected type changes the next fill value; it is not a fixed memo.
    await wrapper.find('select.form-ctrl').setValue('T')
    await wrapper.find('.title-fill-btn').trigger('click')
    expect((wrapper.find('input.form-ctrl').element as HTMLInputElement).value).toBe(expectedTask)

    // Repeated clicks are idempotent and never cycle the locale.
    await wrapper.find('.title-fill-btn').trigger('click')
    expect((wrapper.find('input.form-ctrl').element as HTMLInputElement).value).toBe(expectedTask)
    expect(i18n.global.locale.value).toBe(locale)
  })
})
