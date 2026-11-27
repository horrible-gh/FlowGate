import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { modelessStyle, normalizeTaskbarStyle, taskbarCapacity } from '@/core/overlay/modelessRegistry'

vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'classic' } } })), patchRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'classic' } } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; modelessStyle.value = 'flowgate'; vi.restoreAllMocks() })
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}

describe('classic taskbar style', () => {
  it('is a known style whose named items overflow like FlowGate rather than like icon tiles', () => {
    expect(normalizeTaskbarStyle('classic')).toBe('classic')
    expect(taskbarCapacity(2000, 5, 'classic')).toBe(5)
    expect(taskbarCapacity(700, 20, 'classic')).toBeLessThan(taskbarCapacity(700, 20, 'modern'))
    expect(taskbarCapacity(420, 9, 'classic')).toBeLessThan(9)
  })
  it('keeps the names on the menu button and the designer item', async () => {
    const wrapper = bar()
    await flushPromises()
    expect(wrapper.get('[data-test="designer-taskbar"]').attributes('data-taskbar-style')).toBe('classic')
    expect(wrapper.get('.menu-trigger .item-label').text()).toBe(String(i18n.global.t('gui.taskbar.menu')))
    expect(wrapper.get('.designer-item b').text()).toBe(String(i18n.global.t('gui.nav.designer.title')))
  })
  it('opens a two-level start menu: one line per group on the first level, the cards in a sub-menu', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    const menu = wrapper.get('.start-classic')
    expect(wrapper.find('.menu-board').exists()).toBe(false)
    expect(wrapper.find('.start-box').exists()).toBe(false)
    // First level: only the two group lines, each with an arrow; no cards yet.
    expect(menu.findAll('.classic-group').map(b => b.attributes('data-group'))).toEqual(['ai', 'settings'])
    expect(menu.get('[data-group="ai"] b').text()).toBe(String(i18n.global.t('gui.taskbar.group_ai')))
    expect(menu.get('[data-group="settings"] b').text()).toBe(String(i18n.global.t('gui.taskbar.group_settings')))
    expect(menu.findAll('.classic-group-arrow')).toHaveLength(2)
    expect(menu.get('[data-group="ai"]').attributes('aria-haspopup')).toBe('menu')
    expect(menu.findAll('.classic-card')).toHaveLength(0)
    expect(document.activeElement?.getAttribute('data-group')).toBe('ai')
    // Pressing a line opens its cards beside it.
    await menu.get('[data-group="ai"]').trigger('click')
    await flushPromises()
    expect(menu.get('[data-group="ai"]').attributes('aria-expanded')).toBe('true')
    expect(menu.get('.start-classic-sub').findAll('.classic-card').map(b => b.attributes('data-card'))).toEqual(['my-ai', 'ai-other', 'ai-policy'])
    expect(menu.get('[data-card="my-ai"] small').text()).toBe(String(i18n.global.t('gui.taskbar.my_ai_desc')))
    await menu.get('[data-group="settings"]').trigger('click')
    await flushPromises()
    expect(menu.findAll('.start-classic-sub')).toHaveLength(1)
    expect(menu.get('.start-classic-sub').findAll('.classic-card').map(b => b.attributes('data-card'))).toEqual(['theme', 'agents', 'account', 'security'])
    expect(menu.get('[data-group="ai"]').attributes('aria-expanded')).toBe('false')
  })
  it('opens a sub-menu when the pointer rests on a line', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = bar()
      await flushPromises()
      await wrapper.get('.menu-trigger').trigger('click')
      await wrapper.get('[data-group="settings"]').trigger('pointerenter')
      expect(wrapper.find('.start-classic-sub').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(200)
      await flushPromises()
      expect(wrapper.get('.start-classic-sub').attributes('data-group')).toBe('settings')
    } finally { vi.useRealTimers() }
  })
  it('moves with the arrow keys between the levels and steps back one level on Esc', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    const key = async (name: string) => { (document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })); await flushPromises() }
    await key('ArrowDown')
    expect(document.activeElement?.getAttribute('data-group')).toBe('settings')
    await key('ArrowRight')
    expect(document.activeElement?.getAttribute('data-card')).toBe('theme')
    await key('ArrowDown')
    expect(document.activeElement?.getAttribute('data-card')).toBe('agents')
    await key('ArrowLeft')
    expect(wrapper.find('.start-classic-sub').exists()).toBe(false)
    expect(document.activeElement?.getAttribute('data-group')).toBe('settings')
    await key('Enter')
    expect(document.activeElement?.getAttribute('data-card')).toBe('theme')
    await key('Escape')
    expect(wrapper.find('.start-classic').exists()).toBe(true)
    expect(wrapper.find('.start-classic-sub').exists()).toBe(false)
    expect(document.activeElement?.getAttribute('data-group')).toBe('settings')
    await key('Escape')
    expect(wrapper.find('.start-classic').exists()).toBe(false)
  })
  it('opens the chosen window and marks it with a dot on its card and on its group line', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await wrapper.get('[data-group="settings"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-card="theme"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.start-classic').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click')
    expect(wrapper.find('.start-classic-sub').exists()).toBe(false)
    expect(wrapper.get('[data-group="settings"]').find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-group="ai"]').find('[data-test="menu-card-dot"]').exists()).toBe(false)
    await wrapper.get('[data-group="settings"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-card="theme"]').attributes('data-card-state')).toBe('open')
    expect(wrapper.get('[data-card="theme"]').find('[data-test="menu-card-dot"]').exists()).toBe(true)
    expect(wrapper.get('[data-card="agents"]').find('[data-test="menu-card-dot"]').exists()).toBe(false)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.start-classic').exists()).toBe(false)
  })
  it('gives modeless windows the classic title bar while the taskbar is shown', async () => {
    bar()
    await flushPromises()
    const shell = mount(ModelessShell, { props: { open: true, title: 'Doc' }, attachTo: document.body })
    mounted.push(shell)
    await flushPromises()
    expect(document.querySelector('.ui-modeless-panel')?.getAttribute('data-taskbar-style')).toBe('classic')
  })
})

