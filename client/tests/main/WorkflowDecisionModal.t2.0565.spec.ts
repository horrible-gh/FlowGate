import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))
import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

describe('T2 proposal pair in workflow dialog', () => {
  it('renders T2 in the picker and inserts TR2 immediately after it', async () => {
    const wrapper = mount(WorkflowDecisionModal, {
      props: { visible: true },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    const tags = (selector: string) =>
      wrapper.findAll(selector + ' .doc-tag').map((el) => el.text())
    expect(tags('.wdm-type-btn')).toContain('T2')
    expect(tags('.wdm-auto-item-btn')).toContain('TR2')
    const button = wrapper.findAll('.wdm-type-btn')
      .find((el) => el.find('.doc-tag').text() === 'T2')
    expect(button).toBeTruthy()
    await button!.trigger('click')
    expect(tags('.wdm-seq-item')).toEqual(['T2', 'TR2'])
    wrapper.unmount()
  })
})
