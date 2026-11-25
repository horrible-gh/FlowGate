import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { modelessStyle, normalizeTaskbarStyle, taskbarCapacity } from '@/core/overlay/modelessRegistry'

vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'modern' } } })), patchRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'modern' } } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; modelessStyle.value = 'flowgate'; vi.restoreAllMocks() })
function bar() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1100)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}

describe('modern taskbar style', () => {
  it('is a known style and fits more icon items than FlowGate cards', () => {
    expect(normalizeTaskbarStyle('modern')).toBe('modern')
    expect(taskbarCapacity(600, 20, 'modern')).toBeGreaterThan(taskbarCapacity(600, 20, 'flowgate'))
    expect(taskbarCapacity(2000, 5, 'modern')).toBe(5)
  })
  it('marks the bar, shows names in the name tip at once and opens the icon box with all six cards', async () => {
    const wrapper = bar()
    await flushPromises()
    expect(wrapper.get('[data-test="designer-taskbar"]').attributes('data-taskbar-style')).toBe('modern')
    const menu = wrapper.get('.menu-trigger')
    expect(menu.attributes('aria-label')).toBe(String(i18n.global.t('gui.taskbar.menu')))
    expect(menu.attributes('title')).toBeUndefined()
    expect(wrapper.get('.designer-item').attributes('aria-label')).toBe(String(i18n.global.t('gui.nav.designer.title')))
    await menu.trigger('focus')
    await flushPromises()
    expect(wrapper.get('[data-test="taskbar-tip"]').text()).toBe(String(i18n.global.t('gui.taskbar.menu')))
    await menu.trigger('blur')
    expect(wrapper.find('[data-test="taskbar-tip"]').exists()).toBe(false)
    await menu.trigger('click')
    expect(wrapper.find('.start-box').exists()).toBe(true)
    expect(wrapper.find('.menu-board').exists()).toBe(false)
    expect(wrapper.findAll('.start-tile').map(b => b.attributes('data-card'))).toEqual(['my-ai', 'ai-other', 'ai-policy', 'theme', 'agents', 'account', 'security'])
    await wrapper.get('[data-card="theme"]').trigger('click')
    expect(wrapper.find('.start-box').exists()).toBe(false)
  })
  it('gives modeless windows the modern title bar while the taskbar is shown', async () => {
    bar()
    await flushPromises()
    const shell = mount(ModelessShell, { props: { open: true, title: 'Doc' }, attachTo: document.body })
    mounted.push(shell)
    await flushPromises()
    expect(document.querySelector('.ui-modeless-panel')?.getAttribute('data-taskbar-style')).toBe('modern')
  })
})

