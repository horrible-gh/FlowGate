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
const launchpad = () => document.querySelector<HTMLElement>('.taskbar-popup.launchpad')

describe('dock taskbar style', () => {
  it('is a known style whose icon tiles fit more windows than FlowGate cards', () => {
    expect(normalizeTaskbarStyle('dock')).toBe('dock')
    expect(taskbarCapacity(600, 20, 'dock')).toBeGreaterThan(taskbarCapacity(600, 20, 'flowgate'))
    expect(taskbarCapacity(2000, 5, 'dock')).toBe(5)
    expect(taskbarCapacity(400, 20, 'dock')).toBeLessThan(20)
  })
  it('groups icon-only items with hover names and opens a full-screen launchpad with all six icons', async () => {
    const wrapper = bar()
    await flushPromises()
    expect(wrapper.get('[data-test="designer-taskbar"]').attributes('data-taskbar-style')).toBe('dock')
    const group = wrapper.get('.taskbar-group')
    expect(group.find('.menu-trigger').exists()).toBe(true)
    expect(group.element.firstElementChild?.classList.contains('menu-trigger')).toBe(true)
    expect(wrapper.get('.designer-item').attributes('title')).toBe(String(i18n.global.t('gui.nav.designer.title')))
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    const pad = launchpad()
    expect(pad).not.toBeNull()
    expect(pad!.parentElement).toBe(document.body)
    expect(wrapper.find('.menu-board').exists()).toBe(false)
    expect([...pad!.querySelectorAll('.launch-tile')].map(b => b.getAttribute('data-card'))).toEqual(['my-ai', 'question-policy', 'theme', 'agents', 'account', 'security'])
    // No group headings above the icons, as on a real launchpad: one grid holds all six tiles.
    expect(pad!.querySelectorAll('.launch-grid')).toHaveLength(1)
    expect(pad!.querySelector('.launch-grid')!.querySelectorAll('.launch-tile')).toHaveLength(6)
    const padText = pad!.textContent ?? ''
    expect(padText).not.toContain(String(i18n.global.t('gui.taskbar.group_ai')))
    expect(padText).not.toContain(String(i18n.global.t('gui.taskbar.group_settings')))
    expect(document.activeElement?.getAttribute('data-card')).toBe('my-ai')
  })
  it('closes when an empty area is pressed or Esc is pressed, but not when an icon is pressed', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    launchpad()!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(launchpad()).toBeNull()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    launchpad()!.querySelector('.launch-tile')!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(launchpad()).not.toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(launchpad()).toBeNull()
  })
  it('opens the chosen window from the launchpad and closes it', async () => {
    const wrapper = bar()
    await flushPromises()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    ;(launchpad()!.querySelector('[data-card="theme"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(launchpad()).toBeNull()
    await wrapper.get('.menu-trigger').trigger('click')
    await nextTick()
    expect(launchpad()!.querySelector('[data-card="theme"]')?.getAttribute('data-card-state')).toBe('open')
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
