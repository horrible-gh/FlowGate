import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser, useCurrentUser } from '@/core/auth/currentUser'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import * as settingsApi from '@/features/settings/api'

// The Designer's agents window: the menu card opens my own Agent list (the personal agent settings screen
// without its page frame) over the Designer, and brings that one window back instead of opening a second.
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: {} } })), patchRequest: vi.fn(async () => ({ data: { settings: {} } })) }))
vi.mock('@/features/settings/api', async (original) => ({ ...await original<typeof import('@/features/settings/api')>(), listPersonalAgents: vi.fn(), listSystemAgents: vi.fn(), createPersonalAgent: vi.fn(), deletePersonalAgent: vi.fn() }))
registerAppMessages(i18n)
const agent = (id: string, name: string) => ({
  agent_id: id, name, enabled: true, location: 'remote', connection_mode: 'agent_pull', local_executable_path: null, remote_workdir: null,
  max_concurrent_jobs: 4, running_jobs: 0, queued_jobs: 0, workspace_cleanup_enabled: true, workspace_cleanup_retention_hours: 24,
  configured_capabilities: { ai_cli: true, ai_api: false, storage: false }, reported_capabilities: { ai_cli: true, ai_api: null, storage: null },
  effective_capabilities: { ai_cli: true, ai_api: false, storage: false }, registration_status: 'registered', credential_status: 'active',
  has_registration_history: true, enrollment_status: 'none', enrollment_expires_at: null, online_status: 'offline', last_seen_at: null,
  agent_version: null, protocol_version: null, protocol_compatibility: 'unknown', last_rejected_protocol_version: null, last_protocol_rejection_at: null, os: null, architecture: null,
}) as unknown as Awaited<ReturnType<typeof settingsApi.listPersonalAgents>>[number]
const mounted: VueWrapper[] = []
const clickAction = (action: string) => (document.querySelector(`[data-modeless-action="${action}"]`) as HTMLButtonElement).click()
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return { wrapper, router }
}
const openMenu = async (wrapper: VueWrapper) => { await wrapper.get('.menu-trigger').trigger('click'); await flushPromises() }
const body = () => document.querySelector('[data-test="me-agents-window"]')

describe('v02test.0055 에이전트 window', () => {
  let previousUser: ReturnType<typeof useCurrentUser>['user']['value']
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    previousUser = useCurrentUser().user.value
    sessionStorage.setItem('fg_access_token', 'token')
    setCurrentUser({ username: 'me', email: null, isAdmin: false })
    vi.mocked(settingsApi.listPersonalAgents).mockResolvedValue([agent('a1', '내 노트북'), agent('a2', '집 서버')])
  })
  afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; sessionStorage.clear(); setCurrentUser(previousUser); vi.restoreAllMocks(); vi.resetAllMocks() })

  it('puts the card next to the theme card in the settings group', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const rows = wrapper.findAll('.menu-group-row')
    expect(rows[1].findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['theme', 'agents'])
    expect(wrapper.get('[data-card="agents"]').text()).toContain(i18n.global.t('gui.taskbar.agents'))
    expect(wrapper.get('[data-card="agents"]').attributes('data-card-state')).toBe('closed')
  })

  it('opens my own Agent list over the Designer, without the settings page frame and without leaving the page', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="agents"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll('[data-test="me-agents-window"]')).toHaveLength(1)
    expect(document.querySelector('.ui-modeless-title')?.textContent?.trim()).toBe(i18n.global.t('gui.taskbar.agents_window'))
    // My Agents only: the personal API is read once, the system one never.
    expect(settingsApi.listPersonalAgents).toHaveBeenCalledTimes(1)
    expect(settingsApi.listSystemAgents).not.toHaveBeenCalled()
    expect([...body()!.querySelectorAll('[data-testid="agent-list"] .agent-row strong')].map(n => n.textContent)).toEqual(['내 노트북', '집 서버'])
    expect(body()!.querySelector('[data-panel="personal-agents"]')).not.toBeNull()
    expect(body()!.querySelector('[data-testid="agent-new"]')).not.toBeNull()
    // No settings navigation or page frame inside the window.
    expect(body()!.querySelector('.settings-shell-view, .fgs-layout, .fgs-nav')).toBeNull()
  })

  it('leaves the query of the page underneath alone', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    await router.push('/?agent=a1&flow=7'); await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ agent: 'a1', flow: '7' })
    await openMenu(wrapper)
    await wrapper.get('[data-card="agents"]').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ agent: 'a1', flow: '7' })
    expect(document.querySelector('[data-testid="agent-detail"]')).toBeNull()
  })

  it('opens the registration dialog from the window', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="agents"]').trigger('click'); await flushPromises()
    ;(body()!.querySelector('[data-testid="agent-new"]') as HTMLButtonElement).click(); await flushPromises()
    expect(document.querySelector('[data-testid="agent-create-form"]')).not.toBeNull()
    expect(settingsApi.createPersonalAgent).not.toHaveBeenCalled()
  })

  it('keeps one window through minimize and restore, and closes back to a card with no dot', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="agents"]')
    await card().trigger('click'); await flushPromises()
    clickAction('minimize'); await flushPromises()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    expect(wrapper.get('.minimized-item').text()).toBe('□' + i18n.global.t('gui.taskbar.agents_window'))
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-card="theme"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(document.querySelectorAll('[data-test="me-agents-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    expect(settingsApi.listPersonalAgents).toHaveBeenCalledTimes(1)
    clickAction('close'); await flushPromises()
    expect(body()).toBeNull()
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('closed')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(false)
  })
})
