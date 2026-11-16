import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import Header from '@/features/designer/top/DesignerHeader.vue'

const signOut = vi.hoisted(() => vi.fn(async () => {}))
vi.mock('@/core/auth/logout', () => ({ logout: signOut }))
vi.mock('@/features/designer/top/notifications/useNotificationBadge', () => ({ useNotificationBadge: () => 0 }))
registerAppMessages(i18n)
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => { signOut.mockClear(); i18n.global.locale.value = 'ko' })
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks(); setCurrentUser(null) })
async function setup(admin = false) {
  setCurrentUser({ username: 'tester', email: 'tester@example.com', isAdmin: admin })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/designer', name: 'designer', component: { template: '<div />' } },
    { path: '/settings/me/account', name: 'settings-me-account', component: { template: '<div />' } },
    { path: '/settings/me/security', name: 'settings-me-security', component: { template: '<div />' } },
  ] })
  await router.push('/designer')
  wrapper = mount(Header, { attachTo: document.body, global: { plugins: [i18n, router], stubs: { GlobalSearch: true, DesignerNotifications: true } } })
  return { router, w: wrapper }
}
describe('Designer personal account entry', () => {
  it.each([false, true])('settings opens the personal account for admin=%s', async (admin) => {
    const { router, w } = await setup(admin)
    await w.get('[data-test="designer-header-settings"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('settings-me-account')
  })
  it('shows exactly three working account actions and the signed in identity', async () => {
    const { router, w } = await setup()
    await w.get('[data-test="designer-header-user"]').trigger('click')
    expect(w.get('[role="menu"]').text()).toContain('tester@example.com')
    expect(w.findAll('[role="menuitem"]').map(b => b.text())).toEqual(['내 프로필', '비밀번호 변경', '로그아웃'])
    await w.get('[data-test="account-profile"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('settings-me-account')
    expect(w.find('[role="menu"]').exists()).toBe(false)
    await w.get('[data-test="designer-header-user"]').trigger('click')
    await w.get('[data-test="account-password"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('settings-me-security')
  })
  it('requires confirmation before calling the existing logout', async () => {
    const { w } = await setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await w.get('[data-test="designer-header-user"]').trigger('click')
    await w.get('[data-test="account-logout"]').trigger('click')
    expect(signOut).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    await w.get('[data-test="designer-header-user"]').trigger('click')
    await w.get('[data-test="account-logout"]').trigger('click')
    expect(signOut).toHaveBeenCalledTimes(1)
  })
  it('supports arrow keys, Escape focus return, and outside dismissal', async () => {
    const { w } = await setup()
    const trigger = w.get('[data-test="designer-header-user"]')
    await trigger.trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(w.get('[data-test="account-profile"]').element)
    await w.get('[role="menu"]').trigger('keydown', { key: 'ArrowUp' })
    expect(document.activeElement).toBe(w.get('[data-test="account-logout"]').element)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger.element)
    await trigger.trigger('click')
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(w.find('[role="menu"]').exists()).toBe(false)
  })
  it.each([['ko', '내 프로필'], ['en', 'My profile'], ['ja', 'プロフィール']])('translates the menu in %s', async (locale, label) => {
    const { w } = await setup()
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
    await w.get('[data-test="designer-header-user"]').trigger('click')
    expect(w.get('[data-test="account-profile"]').text()).toBe(label)
  })
})
