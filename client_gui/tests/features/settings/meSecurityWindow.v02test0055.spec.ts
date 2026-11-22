import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { logout } from '@/core/auth/logout'
import { setCurrentUser, useCurrentUser } from '@/core/auth/currentUser'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import * as meApi from '@/features/settings/meApi'

// The Designer's security window: the menu card opens the password change and my sign-in sessions (the
// security settings screen without its page frame) over the Designer, and brings that one window back
// instead of opening a second.
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: {} } })), patchRequest: vi.fn(async () => ({ data: { settings: {} } })) }))
vi.mock('@/features/settings/meApi', async (original) => ({ ...await original<typeof import('@/features/settings/meApi')>(), listSessions: vi.fn(), revokeSession: vi.fn(), revokeOtherSessions: vi.fn(), changePassword: vi.fn() }))
vi.mock('@/core/auth/logout', () => ({ logout: vi.fn(async () => {}) }))
registerAppMessages(i18n)
const session = (id: string, current: boolean): meApi.AuthSession => ({ session_id: id, device_label: current ? 'Chrome · Windows' : 'Firefox · Linux', ip_display: '10.0.0.' + id.slice(1), created_at: '2026-11-20T00:00:00Z', last_used_at: '2026-11-22T00:00:00Z', is_current: current }) as meApi.AuthSession
const mounted: VueWrapper[] = []
const clickAction = (action: string) => (document.querySelector(`[data-modeless-action="${action}"]`) as HTMLButtonElement).click()
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return { wrapper, router }
}
const openMenu = async (wrapper: VueWrapper) => { await wrapper.get('.menu-trigger').trigger('click'); await flushPromises() }
const body = () => document.querySelector('[data-test="me-security-window"]')
const click = async (selector: string) => { (document.querySelector(selector) as HTMLElement).click(); await flushPromises() }

describe('v02test.0055 security window', () => {
  let previousUser: ReturnType<typeof useCurrentUser>['user']['value']
  let rows: meApi.AuthSession[]
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    previousUser = useCurrentUser().user.value
    sessionStorage.setItem('fg_access_token', 'token')
    setCurrentUser({ username: 'me', email: null, isAdmin: false })
    rows = [session('s1', true), session('s2', false), session('s3', false)]
    vi.mocked(meApi.listSessions).mockImplementation(async () => rows.map(r => ({ ...r })))
    vi.mocked(meApi.revokeSession).mockImplementation(async (id: string) => { rows = rows.filter(r => r.session_id !== id); return undefined as never })
    vi.mocked(meApi.revokeOtherSessions).mockImplementation(async () => { const before = rows.length; rows = rows.filter(r => r.is_current); return { revoked_count: before - rows.length } as never })
  })
  afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; sessionStorage.clear(); setCurrentUser(previousUser); i18n.global.locale.value = 'ko'; vi.restoreAllMocks(); vi.clearAllMocks() })

  it('puts the card last in the settings group, after the account card', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const groups = wrapper.findAll('.menu-group-row')
    expect(groups[1].findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['theme', 'agents', 'account', 'security'])
    expect(wrapper.get('[data-card="security"]').text()).toContain(i18n.global.t('gui.taskbar.security'))
    expect(wrapper.get('[data-card="security"]').attributes('data-card-state')).toBe('closed')
    // Nothing is read for the window until its card is pressed.
    expect(meApi.listSessions).not.toHaveBeenCalled()
  })

  it('opens the password row and my sessions over the Designer, without the settings page frame and without leaving the page', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="security"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll('[data-test="me-security-window"]')).toHaveLength(1)
    expect(document.querySelector('.ui-modeless-title')?.textContent?.trim()).toBe(i18n.global.t('gui.taskbar.security_window'))
    expect(meApi.listSessions).toHaveBeenCalledTimes(1)
    expect([...body()!.querySelectorAll('[data-session]')].map(n => n.getAttribute('data-session'))).toEqual(['s1', 's2', 's3'])
    expect(body()!.querySelector('[data-session="s1"] [data-testid="session-current"]')).not.toBeNull()
    expect(body()!.querySelector('[data-session="s1"] [data-testid="revoke-session"]')).toBeNull()
    expect(body()!.querySelector('[data-testid="password-open"]')).not.toBeNull()
    expect(body()!.querySelector('[data-testid="current-password"]')).toBeNull()
    expect(body()!.querySelector('.settings-shell-view, .fgs-layout, .fgs-nav')).toBeNull()
  })

  it('ends another session from the window and re-reads the list; the current one is only offered sign-out', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="security"]').trigger('click'); await flushPromises()
    await click('[data-test="me-security-window"] [data-session="s2"] [data-testid="revoke-session"]')
    expect(meApi.revokeSession).toHaveBeenCalledWith('s2')
    expect(meApi.listSessions).toHaveBeenCalledTimes(2)
    expect([...body()!.querySelectorAll('[data-session]')].map(n => n.getAttribute('data-session'))).toEqual(['s1', 's3'])
    // Signing out the others asks first: cancel sends nothing, confirm sends the one request.
    await click('[data-test="me-security-window"] [data-testid="revoke-others"]')
    await click('[data-testid="confirm-cancel"]')
    expect(meApi.revokeOtherSessions).not.toHaveBeenCalled()
    await click('[data-test="me-security-window"] [data-testid="revoke-others"]')
    await click('[data-testid="confirm-ok"]')
    expect(meApi.revokeOtherSessions).toHaveBeenCalledTimes(1)
    expect([...body()!.querySelectorAll('[data-session]')].map(n => n.getAttribute('data-session'))).toEqual(['s1'])
    expect(body()!.querySelector('[data-testid="revoke-others"]')).toBeNull()
    expect(logout).not.toHaveBeenCalled()
    await click('[data-test="me-security-window"] [data-testid="logout-current"]')
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('changes the password from the window: a mismatch sends nothing, a match sends the one request', async () => {
    vi.mocked(meApi.changePassword).mockResolvedValue(undefined as never)
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="security"]').trigger('click'); await flushPromises()
    await click('[data-test="me-security-window"] [data-testid="password-open"]')
    const fill = async (testid: string, value: string) => { const input = body()!.querySelector(`[data-testid="${testid}"]`) as HTMLInputElement; input.value = value; input.dispatchEvent(new Event('input')); await flushPromises() }
    const submit = async () => { body()!.querySelector('form[data-card="password"]')!.dispatchEvent(new Event('submit', { cancelable: true })); await flushPromises() }
    await fill('current-password', 'OldP@ssw0rd123'); await fill('new-password', 'NewP@ssw0rd123'); await fill('confirm-password', 'Different123')
    await submit()
    expect(meApi.changePassword).not.toHaveBeenCalled()
    expect(body()!.querySelector('[data-testid="password-error"]')!.textContent).toContain(i18n.global.t('gui.settings.self.security.mismatch'))
    await fill('confirm-password', 'NewP@ssw0rd123')
    await submit()
    expect(meApi.changePassword).toHaveBeenCalledWith('OldP@ssw0rd123', 'NewP@ssw0rd123')
    expect(body()!.querySelector('[data-testid="password-done"]')).not.toBeNull()
  })

  it('keeps one window through minimize and restore, and closes back to a card with no dot', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="security"]')
    await card().trigger('click'); await flushPromises()
    clickAction('minimize'); await flushPromises()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    expect(wrapper.get('.minimized-item').text()).toBe('□' + i18n.global.t('gui.taskbar.security_window'))
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-card="account"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(document.querySelectorAll('[data-test="me-security-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    // The list is not read again by minimize and restore.
    expect(meApi.listSessions).toHaveBeenCalledTimes(1)
    clickAction('close'); await flushPromises()
    expect(body()).toBeNull()
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('closed')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(false)
  })
})
