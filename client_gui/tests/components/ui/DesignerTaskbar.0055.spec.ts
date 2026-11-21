import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { modelessWindows, modelessTaskbarHeight, taskbarCapacity, normalizeTaskbarStyle } from '@/core/overlay/modelessRegistry'

vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'future' } } })), patchRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'flowgate' } } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })
const tick = async () => { await flushPromises() }
function panel(props = {}) {
  const wrapper = mount(ModelessShell, { props: { open: true, title: 'Document', initialX: 100, initialY: 100, initialWidth: 400, initialHeight: 300, ...props }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }, { path: '/settings/me/account', name: 'settings-me-account', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return { wrapper, router }
}
const clickMinimize = () => (document.querySelector('[data-modeless-action="minimize"]') as HTMLButtonElement).click()

describe('taskbar window lifecycle', () => {
  it('hides the whole minimized shell and restores its geometry and retained body', async () => {
    const { wrapper } = bar()
    const p = panel()
    await tick()
    const element = document.querySelector('.ui-modeless-panel') as HTMLElement
    const before = [element.style.left, element.style.top, element.style.width, element.style.height]
    clickMinimize(); await tick()
    expect(element.style.display).toBe('none')
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    // A taskbar item shows the window title only, with no status word next to it.
    expect(wrapper.get('.minimized-item').text()).toBe('□Document')
    expect(wrapper.find('[role="separator"]').exists()).toBe(true)
    await wrapper.get('.minimized-item').trigger('click'); await tick()
    expect([element.style.left, element.style.top, element.style.width, element.style.height]).toEqual(before)
    expect(element.style.display).not.toBe('none')
    expect(p.emitted('update:minimized')).toEqual([[true], [false]])
    expect(wrapper.find('[role="separator"]').exists()).toBe(false)
    await p.setProps({ open: false }); await tick()
    expect(modelessWindows.value).toHaveLength(0)
  })
  it('uses the controlled update path and reads reactive header slot titles', async () => {
    const { wrapper } = bar()
    const minimized = ref(false)
    const title = ref('First document')
    const host = mount(defineComponent({ setup: () => () => h(ModelessShell, { open: true, minimized: minimized.value, 'onUpdate:minimized': (v: boolean) => { minimized.value = v } }, { header: () => h('b', title.value) }) }), { attachTo: document.body })
    mounted.push(host)
    await tick(); clickMinimize(); await tick()
    title.value = 'Renamed document'; await tick()
    expect(wrapper.get('.minimized-item').text()).toContain('Renamed document')
    await wrapper.get('.minimized-item').trigger('click'); await tick()
    expect(minimized.value).toBe(false)
    minimized.value = true; await tick()
    minimized.value = false; await tick()
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
  })
  it('keeps the old titlebar outside Designer and unregisters on unmount', async () => {
    const p = panel(); await tick(); clickMinimize(); await tick()
    expect(modelessTaskbarHeight.value).toBe(0)
    expect((document.querySelector('.ui-modeless-panel') as HTMLElement).style.display).not.toBe('none')
    p.unmount(); mounted.splice(mounted.indexOf(p), 1)
    expect(modelessWindows.value).toHaveLength(0)
  })
  it('groups overflow and restores a selected window from it', async () => {
    const { wrapper } = bar()
    for (let i = 0; i < 12; i++) { panel({ title: `Window ${i}` }); await tick(); clickMinimize(); await tick() }
    await tick()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(taskbarCapacity(1100, 12))
    expect(wrapper.get('.overflow-trigger').text()).toBe(`▾ +${12 - taskbarCapacity(1100, 12)}`)
    await wrapper.get('.overflow-trigger').trigger('click'); await tick()
    expect(wrapper.findAll('[role="menuitem"]')).toHaveLength(12 - taskbarCapacity(1100, 12))
    await wrapper.get('[role="menuitem"]').trigger('click'); await tick()
    expect(modelessWindows.value.filter(entry => entry.minimized)).toHaveLength(11)
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
  })
  it('opens the theme window from its card and closes the menu on Escape and outside pointer', async () => {
    const { wrapper, router } = bar(); await tick()
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await tick()
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })); await tick()
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    const card = () => wrapper.get('[data-card="theme"]')
    expect(card().attributes('data-card-state')).toBe('closed')
    // The card carries no status wording: a closed window shows nothing, an open or minimized one a dot.
    const cardText = card().text()
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(false)
    await card().trigger('click'); await tick()
    // The card opens a window over the Designer; the page does not change.
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.querySelectorAll('[data-test="me-theme-window"]')).toHaveLength(1)
    expect(document.querySelector('[data-test="me-theme-window"] select')).not.toBeNull()
    clickMinimize(); await tick()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(card().text()).toBe(cardText)
    // A minimized window is brought back by its card, never opened twice.
    await card().trigger('click'); await tick()
    expect(document.querySelectorAll('[data-test="me-theme-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    expect(card().attributes('data-card-state')).toBe('open')
    expect(card().find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(card().text()).toBe(cardText)
    ;(document.querySelector('[data-modeless-action="close"]') as HTMLButtonElement).click(); await tick()
    expect(document.querySelector('[data-test="me-theme-window"]')).toBeNull()
    expect(card().attributes('data-card-state')).toBe('closed')
  })
  it('lists my AI and theme cards by group and opens the my AI window without leaving the page', async () => {
    const { wrapper, router } = bar(); await tick()
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    expect(wrapper.findAll('.menu-card').map(c => c.attributes('data-card'))).toEqual(['my-ai', 'theme'])
    expect(wrapper.findAll('.menu-group-label').map(l => l.text())).toEqual([i18n.global.t('gui.taskbar.group_ai'), i18n.global.t('gui.taskbar.group_settings')])
    const card = () => wrapper.get('[data-card="my-ai"]')
    expect(card().attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await tick()
    expect(router.currentRoute.value.path).toBe('/')
    expect(document.querySelectorAll('[data-test="my-ai-window"]')).toHaveLength(1)
    // The window opens on my own AI unless I have none yet, and leaves the theme card untouched.
    expect(document.querySelector('[data-test="my-ai-window"] .ai-providers-panel')).not.toBeNull()
    clickMinimize(); await tick()
    expect(wrapper.findAll('.minimized-item')).toHaveLength(1)
    await wrapper.get('.menu-trigger').trigger('click'); await tick()
    expect(card().attributes('data-card-state')).toBe('minimized')
    expect(wrapper.get('[data-card="theme"]').attributes('data-card-state')).toBe('closed')
    await card().trigger('click'); await tick()
    expect(document.querySelectorAll('[data-test="my-ai-window"]')).toHaveLength(1)
    expect(wrapper.find('.minimized-item').exists()).toBe(false)
    ;(document.querySelector('[data-modeless-action="close"]') as HTMLButtonElement).click(); await tick()
    expect(document.querySelector('[data-test="my-ai-window"]')).toBeNull()
  })
  it('always falls back to the implemented style', () => {
    expect(normalizeTaskbarStyle('future')).toBe('flowgate')
    expect(normalizeTaskbarStyle(null)).toBe('flowgate')
  })
})
