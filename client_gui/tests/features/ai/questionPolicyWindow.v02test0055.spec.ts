import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import * as policyApi from '@/features/ai/policy/api'

// The Designer's question policy, since v02test.0096 F96-3 the 방침 › 질의 tab of the one AI 관리 window: the 방침 card
// opens the same policy box the AI management screen shows, over the Designer, and brings that one window back (or
// switches it to 방침) instead of opening a second.
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: {} } })), patchRequest: vi.fn(async () => ({ data: { settings: {} } })) }))
vi.mock('@/features/ai/policy/api')
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
const WINDOW = '[data-test="ai-manage-window"]'
const policyBox = () => document.querySelector(`${WINDOW} textarea#question-policy-body`) as HTMLTextAreaElement | null
const clickIn = async (selector: string) => { (document.querySelector(`${WINDOW} ${selector}`) as HTMLButtonElement).click(); await flushPromises() }
const title = () => document.querySelector('.ui-modeless-title')?.textContent?.trim()

describe('v02test.0055 질의 방침 → v02test.0096 AI 관리 window, 방침 tab', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    vi.mocked(policyApi.loadPolicy).mockResolvedValue({ body: '기존 방침', updated_at: null })
    vi.mocked(policyApi.loadProviders).mockResolvedValue([{ id: 'a', name: 'AI A' }])
    vi.mocked(policyApi.savePolicy).mockImplementation(async body => ({ body }))
  })
  afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks(); vi.resetAllMocks() })

  it('puts 내 AI, 기타 설정 and 방침 in the AI group, ahead of the settings group', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    expect(wrapper.findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['my-ai', 'ai-other', 'ai-policy', 'theme', 'agents', 'account', 'security'])
    const rows = wrapper.findAll('.menu-group-row')
    expect(rows[0].findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['my-ai', 'ai-other', 'ai-policy'])
    expect(wrapper.get('[data-card="ai-other"]').text()).toContain('기타 설정')
    expect(wrapper.get('[data-card="ai-policy"]').text()).toContain('방침')
  })

  it('opens the AI 관리 window on 방침 › 질의 with my saved policy as a preview, without leaving the page', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="ai-policy"]')
    expect(card().attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll(WINDOW)).toHaveLength(1)
    expect(title()).toBe('AI 관리 · 방침')
    expect([...document.querySelectorAll(`${WINDOW} .ui-tab`)].map(t => t.textContent?.trim())).toEqual(['내 AI', '기타 설정', '방침'])
    expect(document.querySelector(`${WINDOW} .ui-tab.is-active`)?.textContent?.trim()).toBe('방침')
    // The sub-item list holds 질의 only; the greyed examples of the mock-up are not shown.
    expect([...document.querySelectorAll(`${WINDOW} .policy-nav-item`)].map(b => b.getAttribute('data-section'))).toEqual(['question'])
    expect(document.querySelector(`${WINDOW} [data-test="qp-preview"]`)?.textContent).toContain('기존 방침')
    expect(document.querySelector(`${WINDOW} [data-test="qp-status"]`)?.textContent).toContain('저장됨 · 5 / 4000')
    await clickIn('[data-test="qp-mode-edit"]')
    expect(policyBox()?.value).toBe('기존 방침')
    expect(policyApi.loadPolicy).toHaveBeenCalledTimes(1)
  })

  it('keeps unsaved text through minimize and restore, and never opens a second window', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="ai-policy"]')
    await card().trigger('click'); await flushPromises()
    await clickIn('[data-test="qp-mode-edit"]')
    const box = policyBox()!
    box.value = '고친 방침'; box.dispatchEvent(new Event('input')); await flushPromises()
    clickAction('minimize'); await flushPromises()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    expect(wrapper.get('.minimized-item').text()).toBe('AI 관리 · 방침')
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    // The same window, but not on their tab: the other AI cards show no dot.
    expect(wrapper.get('[data-card="my-ai"]').attributes('data-card-state')).toBe('closed')
    expect(wrapper.get('[data-card="ai-other"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(document.querySelectorAll(WINDOW)).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    expect(policyBox()?.value).toBe('고친 방침')
    expect(policyApi.loadPolicy).toHaveBeenCalledTimes(1)
  })

  it('switches the open window to the tab of another AI card and keeps unsaved policy text', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="ai-policy"]').trigger('click'); await flushPromises()
    await clickIn('[data-test="qp-mode-edit"]')
    const box = policyBox()!
    box.value = '고친 방침'; box.dispatchEvent(new Event('input')); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="ai-other"]').trigger('click'); await flushPromises()
    expect(document.querySelectorAll(WINDOW)).toHaveLength(1)
    expect(title()).toBe('AI 관리 · 기타 설정')
    expect(document.querySelector(`${WINDOW} [data-test="ai-other-settings"]`)).not.toBeNull()
    await openMenu(wrapper)
    expect(wrapper.get('[data-card="ai-other"]').attributes('data-card-state')).toBe('open')
    expect(wrapper.get('[data-card="ai-policy"]').attributes('data-card-state')).toBe('closed')
    // A tab inside the window switches too, and the title and the cards follow it.
    await wrapper.get('.menu-trigger').trigger('click'); await flushPromises()
    await clickIn('.ui-tab:nth-child(3)')
    expect(title()).toBe('AI 관리 · 방침')
    expect(policyBox()?.value).toBe('고친 방침')
    expect(policyApi.loadPolicy).toHaveBeenCalledTimes(1)
  })

  it('saves from the window and closes back to a card with no dot', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="ai-policy"]').trigger('click'); await flushPromises()
    await clickIn('[data-test="qp-mode-edit"]')
    const box = policyBox()!
    box.value = 'UI만 질문'; box.dispatchEvent(new Event('input')); await flushPromises()
    await clickIn('[data-test="qp-save"]')
    expect(policyApi.savePolicy).toHaveBeenCalledWith('UI만 질문')
    clickAction('close'); await flushPromises()
    expect(document.querySelector(WINDOW)).toBeNull()
    await openMenu(wrapper)
    expect(wrapper.get('[data-card="ai-policy"]').attributes('data-card-state')).toBe('closed')
    expect(wrapper.get('[data-card="ai-policy"]').find('[data-test="menu-card-dot"]').exists()).toBe(false)
  })
})
