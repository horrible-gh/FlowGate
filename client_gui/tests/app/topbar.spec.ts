import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import Topbar from '@/app/components/Topbar.vue'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'

const switchGuiGeneration = vi.hoisted(() => vi.fn())
vi.mock('@shared/guiSwitch', () => ({ switchGuiGeneration }))

registerAppMessages(i18n)

// The brand is a RouterLink home (T0011), so the Topbar needs a router to render.
function mountTopbar() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  return mount(Topbar, {
    global: { plugins: [i18n, router] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  switchGuiGeneration.mockReset()
})

afterEach(() => {
  setCurrentUser(null)
})

// T0009 §2/§8/§9: the switch here must mirror v0.1's AppHeader placement/rules:
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

// T0011 §7/§14: brand, search shell, notification control and user area from 01-dashboard.html.
describe('client_gui Topbar mockup elements (T0011)', () => {
  it('shows the FlowGate brand as a link home with its tagline', () => {
    const wrapper = mountTopbar()

    const brand = wrapper.get('a.gui-brand')
    expect(brand.attributes('href')).toBe('/')
    expect(brand.attributes('aria-label')).toBe('FlowGate 대시보드로 이동')
    expect(wrapper.get('.gui-brand-title').text()).toBe('FlowGate')
    expect(wrapper.get('.gui-brand-sub').text()).toBe('Turn Ideas into Working Flow')
    expect(wrapper.findAll('.gui-brandmark i')).toHaveLength(3)

    wrapper.unmount()
  })

  it('renders search as a disabled, labeled shell rather than a working search', () => {
    const wrapper = mountTopbar()

    const input = wrapper.get('.gui-search input')
    expect(input.attributes('disabled')).toBeDefined()
    expect(input.attributes('placeholder')).toBe('프로젝트, 그룹, 파일, 설정 등을 검색하세요...')
    expect(input.attributes('aria-label')).toBe('검색 (준비 중)')
    expect(wrapper.get('.gui-search').attributes('role')).toBe('search')
    expect(wrapper.get('.gui-kbd').text()).toBe('Ctrl + K')

    wrapper.unmount()
  })

  it('renders the icon-only notification bell as a disabled button with an accessible name', () => {
    const wrapper = mountTopbar()

    const bell = wrapper.get('button.gui-bell')
    expect(bell.attributes('disabled')).toBeDefined()
    expect(bell.attributes('aria-label')).toBe('알림 (준비 중)')

    wrapper.unmount()
  })

  it('shows the signed-in user from core auth state, and nothing invented when unknown', async () => {
    const wrapper = mountTopbar()
    expect(wrapper.find('.gui-user').exists()).toBe(false)

    setCurrentUser({ username: 'kim.flowgate', email: null, isAdmin: true })
    await wrapper.vm.$nextTick()

    expect(wrapper.get('.gui-avatar').text()).toBe('K')
    expect(wrapper.get('.gui-user-name').text()).toBe('kim.flowgate')
    expect(wrapper.get('.gui-user-name').attributes('title')).toBe('kim.flowgate')
    expect(wrapper.get('.gui-user-sub').text()).toBe('관리자')

    setCurrentUser({ username: 'lee', email: null, isAdmin: false })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.gui-user-sub').text()).toBe('사용자')

    wrapper.unmount()
  })
})
