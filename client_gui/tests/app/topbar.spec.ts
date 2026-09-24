import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import Topbar from '@/app/components/Topbar.vue'

const switchGuiGeneration = vi.hoisted(() => vi.fn())
vi.mock('@shared/guiSwitch', () => ({ switchGuiGeneration }))

function mountTopbar() {
  return mount(Topbar, {
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  switchGuiGeneration.mockReset()
})

// T0009 §2/§8/§9: client_gui has no App Shell yet (T#1 scope was a bare placeholder), so
// this is a new component. The switch here must mirror v0.1's AppHeader placement/rules:
// labeled (not icon-only), keyboard-focusable, low-priority secondary action.
describe('client_gui Topbar GUI-generation switch (T0009)', () => {
  it('renders a labeled switch button to v0.1', () => {
    const wrapper = mountTopbar()

    const button = wrapper.get('.gui-switch-btn')
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.text()).toBe('v0.1로 전환')

    wrapper.unmount()
  })

  it('performs the switch to v0.1 (legacy) on click', async () => {
    const wrapper = mountTopbar()

    await wrapper.get('.gui-switch-btn').trigger('click')

    expect(switchGuiGeneration).toHaveBeenCalledTimes(1)
    expect(switchGuiGeneration).toHaveBeenCalledWith('legacy')

    wrapper.unmount()
  })

  it('lives in the shared header nav, not inside a Dashboard-owned element', () => {
    const wrapper = mountTopbar()

    expect(wrapper.get('.app-header').find('.header-nav .gui-switch-btn').exists()).toBe(true)

    wrapper.unmount()
  })
})
