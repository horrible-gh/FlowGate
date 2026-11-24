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
  it('opens a start menu with six cards in two groups split by a line, without group headings', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    const menu = wrapper.get('.start-classic')
    expect(wrapper.find('.menu-board').exists()).toBe(false)
    expect(wrapper.find('.start-box').exists()).toBe(false)
    expect(menu.findAll('.classic-card').map(b => b.attributes('data-card'))).toEqual(['my-ai', 'question-policy', 'theme', 'agents', 'account', 'security'])
    expect(menu.findAll('.start-classic-group').map(g => g.findAll('.classic-card').length)).toEqual([2, 4])
    expect(menu.findAll('.start-classic-sep')).toHaveLength(1)
    expect(menu.text()).not.toContain(String(i18n.global.t('gui.taskbar.group_ai')))
    expect(menu.text()).not.toContain(String(i18n.global.t('gui.taskbar.group_settings')))
    expect(menu.get('[data-card="my-ai"] small').text()).toBe(String(i18n.global.t('gui.taskbar.my_ai_desc')))
    expect(document.activeElement?.getAttribute('data-card')).toBe('my-ai')
  })
  it('opens the chosen window, marks it with a dot and closes on Esc', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await wrapper.get('[data-card="theme"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.start-classic').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click')
    expect(wrapper.get('[data-card="theme"]').attributes('data-card-state')).toBe('open')
    expect(wrapper.get('[data-card="theme"]').find('[data-test="menu-card-dot"]').exists()).toBe(true)
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
