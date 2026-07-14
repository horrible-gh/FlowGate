import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWarningDialog from '@main/components/ContinuousWarningDialog.vue'
import { useAiProviderStore } from '@main/stores/aiProvider'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: {
    providers: [
      { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
      { id: 'aip_two', name: 'Two', exec_type: 'api', kind: 'openai' },
    ],
    default_provider_id: 'aip_one',
  } })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ContinuousWarningDialog', () => {
  it('synchronizes the execution provider and exposes all execution methods', async () => {
    const wrapper = mount(ContinuousWarningDialog, {
      props: {
        visible: true,
        project: 'flowgate',
        stepCount: 2,
        targetLabel: 'report',
        reviewMode: false,
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const store = useAiProviderStore()
    const select = document.querySelector('.cwarn-provider select') as HTMLSelectElement
    expect(select.value).toBe('aip_one')
    select.value = 'aip_two'
    select.dispatchEvent(new Event('change'))
    expect(store.selectedProviderId).toBe('aip_two')

    const consent = document.querySelector('.cwarn-consent input') as HTMLInputElement
    consent.click()
    await flushPromises()
    const buttons = [...document.querySelectorAll<HTMLButtonElement>('.cwarn-footer button')]
    buttons[1].click()
    buttons[2].click()
    buttons[3].click()

    expect(wrapper.emitted('copy-mention')).toHaveLength(1)
    expect(wrapper.emitted('copy-with-message')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    wrapper.unmount()
  })
})