import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import * as policyApi from '@/features/ai/policy/api'

// The Designer's question policy window: the menu card opens the same policy box the AI management screen shows,
// over the Designer, and brings that one window back instead of opening a second.
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
const policyBox = () => document.querySelector('[data-test="question-policy-window"] textarea#question-policy-body') as HTMLTextAreaElement | null

describe('v02test.0055 질의 방침 window', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
    vi.mocked(policyApi.loadPolicy).mockResolvedValue({ body: '기존 방침', updated_at: null })
    vi.mocked(policyApi.loadProviders).mockResolvedValue([{ id: 'a', name: 'AI A' }])
    vi.mocked(policyApi.savePolicy).mockImplementation(async body => ({ body }))
  })
  afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks(); vi.resetAllMocks() })

  it('puts the card next to 내 AI in the AI group, ahead of the settings group', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    expect(wrapper.findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['my-ai', 'question-policy', 'theme', 'agents', 'account'])
    const rows = wrapper.findAll('.menu-group-row')
    expect(rows[0].findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['my-ai', 'question-policy'])
    expect(wrapper.get('[data-card="question-policy"]').text()).toContain(i18n.global.t('gui.taskbar.question_policy'))
  })

  it('opens the policy box over the Designer with my saved policy, without leaving the page', async () => {
    const { wrapper, router } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="question-policy"]')
    expect(card().attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll('[data-test="question-policy-window"]')).toHaveLength(1)
    expect(document.querySelector('.ui-modeless-title')?.textContent?.trim()).toBe(i18n.global.t('gui.taskbar.question_policy_window'))
    expect(policyBox()?.value).toBe('기존 방침')
    expect(policyApi.loadPolicy).toHaveBeenCalledTimes(1)
  })

  it('keeps unsaved text through minimize and restore, and never opens a second window', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    const card = () => wrapper.get('[data-card="question-policy"]')
    await card().trigger('click'); await flushPromises()
    const box = policyBox()!
    box.value = '고친 방침'; box.dispatchEvent(new Event('input')); await flushPromises()
    clickAction('minimize'); await flushPromises()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    expect(wrapper.get('.minimized-item').text()).toBe('□' + i18n.global.t('gui.taskbar.question_policy_window'))
    await openMenu(wrapper)
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-card="my-ai"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await flushPromises()
    expect(document.querySelectorAll('[data-test="question-policy-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    expect(policyBox()?.value).toBe('고친 방침')
    expect(policyApi.loadPolicy).toHaveBeenCalledTimes(1)
  })

  it('saves from the window and closes back to a card with no dot', async () => {
    const { wrapper } = bar(); await flushPromises()
    await openMenu(wrapper)
    await wrapper.get('[data-card="question-policy"]').trigger('click'); await flushPromises()
    const box = policyBox()!
    box.value = 'UI만 질문'; box.dispatchEvent(new Event('input')); await flushPromises()
    ;(document.querySelector('[data-test="question-policy-window"] footer button') as HTMLButtonElement).click(); await flushPromises()
    expect(policyApi.savePolicy).toHaveBeenCalledWith('UI만 질문')
    clickAction('close'); await flushPromises()
    expect(document.querySelector('[data-test="question-policy-window"]')).toBeNull()
    await openMenu(wrapper)
    expect(wrapper.get('[data-card="question-policy"]').attributes('data-card-state')).toBe('closed')
    expect(wrapper.get('[data-card="question-policy"]').find('[data-test="menu-card-dot"]').exists()).toBe(false)
  })
})
