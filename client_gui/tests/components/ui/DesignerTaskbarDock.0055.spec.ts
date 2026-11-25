import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { modelessStyle, normalizeTaskbarStyle, taskbarCapacity } from '@/core/overlay/modelessRegistry'

vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'dock' } } })), patchRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'dock' } } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; modelessStyle.value = 'flowgate'; vi.restoreAllMocks() })
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}
const grid = () => document.querySelector<HTMLElement>('.taskbar-popup.dock-grid')

describe('dock taskbar style', () => {
  it('is a known style whose icon tiles fit more windows than FlowGate cards', () => {
    expect(normalizeTaskbarStyle('dock')).toBe('dock')
    expect(taskbarCapacity(600, 20, 'dock')).toBeGreaterThan(taskbarCapacity(600, 20, 'flowgate'))
    expect(taskbarCapacity(2000, 5, 'dock')).toBe(5)
    expect(taskbarCapacity(400, 20, 'dock')).toBeLessThan(20)
  })
  it('groups icon-only items with hover names and opens one row of all seven icons over the blurred page', async () => {
    const wrapper = bar()
    await flushPromises()
    expect(wrapper.get('[data-test="designer-taskbar"]').attributes('data-taskbar-style')).toBe('dock')
    const group = wrapper.get('.taskbar-group')
    expect(group.find('.menu-trigger').exists()).toBe(true)
    expect(group.element.firstElementChild?.classList.contains('menu-trigger')).toBe(true)
    // Icon-only items carry their name as the accessible name and show it at once in the name tip on hover.
    expect(wrapper.get('.designer-item').attributes('aria-label')).toBe(String(i18n.global.t('gui.nav.designer.title')))
    expect(wrapper.get('.designer-item').attributes('title')).toBeUndefined()
    await wrapper.get('.designer-item').trigger('pointerenter')
    await nextTick()
    expect(wrapper.get('[data-test="taskbar-tip"]').text()).toBe(String(i18n.global.t('gui.nav.designer.title')))
    await wrapper.get('.designer-item').trigger('pointerleave')
    expect(wrapper.find('[data-test="taskbar-tip"]').exists()).toBe(false)
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    const panel = grid()
    expect(panel).not.toBeNull()
    // The page above the dock is blurred by one layer of the taskbar; the panel sits on it.
    expect(wrapper.element.contains(panel)).toBe(true)
    expect(wrapper.find('[data-test="dock-backdrop"]').exists()).toBe(true)
    expect(wrapper.find('.menu-board').exists()).toBe(false)
    expect([...panel!.querySelectorAll('.dock-tile')].map(b => b.getAttribute('data-card'))).toEqual(['my-ai', 'ai-other', 'ai-policy', 'theme', 'agents', 'account', 'security'])
    // One row holds all seven tiles, with no group headings; a single line parts the two groups.
    expect(panel!.querySelectorAll('.dock-grid-tiles')).toHaveLength(1)
    expect(panel!.querySelectorAll('[data-test="dock-grid-divider"]')).toHaveLength(1)
    expect(panel!.querySelector('.dock-tile-icon path')).not.toBeNull()
    expect(panel!.querySelector('.dock-tile-icon')!.textContent).toBe('')
    const panelText = panel!.textContent ?? ''
    expect(panelText).not.toContain(String(i18n.global.t('gui.taskbar.group_ai')))
    expect(panelText).not.toContain(String(i18n.global.t('gui.taskbar.group_settings')))
    expect(document.activeElement?.getAttribute('data-card')).toBe('my-ai')
  })
  it('numbers the tiles left to right so their icons rise one after another', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    const order = [...grid()!.querySelectorAll<HTMLElement>('.dock-tile')].map(tile => tile.style.getPropertyValue('--i'))
    expect(order).toEqual(['0', '1', '2', '3', '4', '5'])
  })
  it('closes the menu when the blurred page is pressed', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    await wrapper.get('[data-test="dock-backdrop"]').trigger('pointerdown')
    await nextTick()
    expect(grid()).toBeNull()
    expect(wrapper.find('[data-test="dock-backdrop"]').exists()).toBe(false)
  })
  it('moves through the tiles with the arrow keys', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    const key = async (name: string) => { (document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })); await nextTick() }
    await key('ArrowRight')
    expect(document.activeElement?.getAttribute('data-card')).toBe('ai-other')
    await key('ArrowDown')
    expect(document.activeElement?.getAttribute('data-card')).toBe('ai-policy')
    await key('ArrowLeft')
    expect(document.activeElement?.getAttribute('data-card')).toBe('ai-other')
  })
  it('closes when the page outside is pressed or Esc is pressed, but not when an icon is pressed', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(grid()).toBeNull()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    grid()!.querySelector('.dock-tile')!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(grid()).not.toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(grid()).toBeNull()
  })
  it('opens the chosen window from the grid and closes it', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    ;(grid()!.querySelector('[data-card="theme"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(grid()).toBeNull()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    expect(grid()!.querySelector('[data-card="theme"]')?.getAttribute('data-card-state')).toBe('open')
  })
  it('gives modeless windows the dock title bar while the taskbar is shown', async () => {
    bar()
    await flushPromises()
    const shell = mount(ModelessShell, { props: { open: true, title: 'Doc' }, attachTo: document.body })
    mounted.push(shell)
    await flushPromises()
    expect(document.querySelector('.ui-modeless-panel')?.getAttribute('data-taskbar-style')).toBe('dock')
  })
})

