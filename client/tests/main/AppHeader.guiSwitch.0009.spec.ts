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

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const switchGuiGeneration = vi.hoisted(() => vi.fn())
vi.mock('@shared/guiSwitch', () => ({ switchGuiGeneration }))

function mountHeader() {
  return mount(AppHeader, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        ProjectSelector: true,
        GitStatusPanel: true,
        teleport: true,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
  switchGuiGeneration.mockReset()
})

// T0009 §2/§3/§9: v0.1's AppHeader carries a low-priority, always-visible (not
// tooltip-only, not icon-only) switch to v0.2, placed in the right-hand nav beside
// Settings/Logout rather than inside Dashboard/Sidebar business UI.
describe('AppHeader GUI-generation switch (T0009)', () => {
  it('renders a labeled, keyboard-focusable switch button inside .header-nav', () => {
    const wrapper = mountHeader()

    const nav = wrapper.get('.header-nav')
    const button = nav.find('.gui-switch-btn')
    expect(button.exists()).toBe(true)
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.text().length).toBeGreaterThan(0)
    expect(button.text()).toBe('v0.2로 전환')

    wrapper.unmount()
  })

  it('performs the switch to v0.2 on click', async () => {
    const wrapper = mountHeader()

    await wrapper.get('.gui-switch-btn').trigger('click')

    expect(switchGuiGeneration).toHaveBeenCalledTimes(1)
    expect(switchGuiGeneration).toHaveBeenCalledWith('app')

    wrapper.unmount()
  })

  it('sits between the Settings/Logout dividers, ahead of the user chip', () => {
    const wrapper = mountHeader()

    const nav = wrapper.get('.header-nav').element
    const children = Array.from(nav.children)
    const switchIdx = children.findIndex((el) => el.classList.contains('gui-switch-btn'))
    const userIdx = children.findIndex((el) => el.classList.contains('hdr-user'))

    expect(switchIdx).toBeGreaterThanOrEqual(0)
    expect(userIdx).toBeGreaterThan(switchIdx)

    wrapper.unmount()
  })
})
