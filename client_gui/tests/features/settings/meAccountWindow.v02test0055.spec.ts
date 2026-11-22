import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import * as sharedApi from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser, useCurrentUser } from '@/core/auth/currentUser'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'

// The Designer's account window: the menu card opens my sign-in information and the display language (the
// account settings screen without its page frame) over the Designer, and brings that one window back instead
// of opening a second. The finished-card retention row is gone from the screen and from the window.
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: {} } })), patchRequest: vi.fn(async () => ({ data: { settings: {} } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
const clickAction = (action: string) => (document.querySelector(`[data-modeless-action="${action}"]`) as HTMLButtonElement).click()
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return { wrapper, router }
}
const openMenu = async (wrapper: VueWrapper) => { await wrapper.get('.menu-trigger').trigger('click'); await flushPromises() }
const body = () => document.querySelector('[data-test="me-account-window"]')

describe('v02test.0055 account window', () => {
  let previousUser: ReturnType<typeof useCurrentUser>['user']['value']
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    localStorage.clear()
    previousUser = useCurrentUser().user.value
    sessionStorage.setItem('fg_access_token', 'token')
    setCurrentUser({ username: 'me', email: 'me@flowgate.local', isAdmin: false })
  })
  afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; sessionStorage.clear(); localStorage.clear(); setCurrentUser(previousUser); i18n.global.locale.value = 'ko'; vi.restoreAllMocks(); vi.clearAllMocks() })

  it('puts the card after the theme and agents cards in the settings group', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const rows = wrapper.findAll('.menu-group-row')
    expect(rows[1].findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['theme', 'agents', 'account', 'security'])
    expect(wrapper.get('[data-card="account"]').text()).toContain(i18n.global.t('gui.taskbar.account'))
    expect(wrapper.get('[data-card="account"]').attributes('data-card-state')).toBe('closed')
  })

  it('opens my sign-in information and the language buttons over the Designer, with no request of its own', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    const before = vi.mocked(sharedApi.getRequest).mock.calls.length
    await openMenu(wrapper)
    await wrapper.get('[data-card="account"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll('[data-test="me-account-window"]')).toHaveLength(1)
    expect(document.querySelector('.ui-modeless-title')?.textContent?.trim()).toBe(i18n.global.t('gui.taskbar.account_window'))
    expect(body()!.querySelector('[data-testid="me-username"]')!.textContent).toBe('me')
    expect(body()!.querySelector('[data-testid="me-email"]')!.textContent).toBe('me@flowgate.local')
    expect([...body()!.querySelectorAll('[data-locale]')].map(n => n.getAttribute('data-locale'))).toEqual(['ko', 'ja', 'en'])
    // The retention row is gone.
    expect(body()!.querySelector('[data-card="retention"], #me-retention')).toBeNull()
    // No settings navigation or page frame inside the window, and nothing was read or written for it.
    expect(body()!.querySelector('.settings-shell-view, .fgs-layout, .fgs-nav')).toBeNull()
    expect(vi.mocked(sharedApi.getRequest).mock.calls.length).toBe(before)
    expect(sharedApi.patchRequest).not.toHaveBeenCalled()
  })

  it('switches the display language from the window: the title, the card and preferred_locale follow', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="account"]').trigger('click'); await flushPromises()
    ;(body()!.querySelector('[data-locale="en"]') as HTMLButtonElement).click(); await flushPromises()
    expect(i18n.global.locale.value).toBe('en')
    expect(localStorage.getItem('preferred_locale')).toBe('en')
    expect(body()!.querySelector('[data-locale="en"]')!.getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelector('.ui-modeless-title')?.textContent?.trim()).toBe('My settings · Account')
    expect(sharedApi.patchRequest).not.toHaveBeenCalled()
  })

  it('keeps one window through minimize and restore, and closes back to a card with no dot', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="account"]')
    await card().trigger('click'); await flushPromises()
    clickAction('minimize'); await flushPromises()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    expect(wrapper.get('.minimized-item').text()).toBe('□' + i18n.global.t('gui.taskbar.account_window'))
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-card="agents"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(document.querySelectorAll('[data-test="me-account-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    clickAction('close'); await flushPromises()
    expect(body()).toBeNull()
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('closed')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(false)
  })
})
