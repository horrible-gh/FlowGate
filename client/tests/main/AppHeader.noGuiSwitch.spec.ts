import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AppHeader from '@main/components/AppHeader.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  serverLogout: vi.fn(),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  delete window.__accessToken__
  i18n.global.locale.value = 'ko'
  getRequest.mockResolvedValue({ data: {} })
})

describe('v0.1 header without GUI-generation switch', () => {
  it('does not render the removed v0.2 transition action', () => {
    const wrapper = mount(AppHeader, {
      global: {
        plugins: [i18n],
        stubs: { RouterLink: { template: '<a><slot /></a>' }, ProjectSelector: true },
      },
    })
    expect(wrapper.find('.gui-switch-btn').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('v0.2로 전환')
    wrapper.unmount()
  })
})
