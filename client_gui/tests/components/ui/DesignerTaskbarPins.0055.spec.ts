import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { KIND_ICONS, PIN_ICONS } from '@/features/designer/shell/taskbarIcons'
import { documentNumberBadge, modelessKindOf, modelessStyle, taskbarCapacity } from '@/core/overlay/modelessRegistry'
import { flowTerminalUi } from '@/features/designer/flowTerminal/state'
import { flowAttachmentUi } from '@/features/designer/flowAttachments/state'

const style = vi.hoisted(() => ({ value: 'modern' }))
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: style.value } } })), patchRequest: vi.fn(async () => ({ data: { settings: {} } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => {
  mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''
  modelessStyle.value = 'flowgate'; style.value = 'modern'
  flowTerminalUi.status = null; flowTerminalUi.currentOpen = false; flowAttachmentUi.currentOpen = false
  vi.restoreAllMocks()
})
function bar(props: Record<string, unknown> = {}, width = 1100) {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(width)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { props, global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}
async function minimizedWindow(props: Record<string, unknown>) {
  const shell = mount(ModelessShell, { props: { open: true, ...props }, attachTo: document.body })
  mounted.push(shell)
  await flushPromises()
  await shell.setProps({ minimized: true })
  await flushPromises()
  return shell
}
const pinKeys = (wrapper: VueWrapper) => wrapper.findAll('[data-test="taskbar-pin"]').map(b => b.attributes('data-pin'))

describe('taskbar pinned buttons', () => {
  it('shows project, attachments, Git and search after [Menu]; the terminal only for someone who may use it', async () => {
    const wrapper = bar({ hasFlow: true })
    await flushPromises()
    expect(pinKeys(wrapper)).toEqual(['project', 'attachments', 'git', 'search'])
    expect(wrapper.get('.taskbar-group').element.children[1].getAttribute('data-pin')).toBe('project')
    flowTerminalUi.status = { enabled: true, allowed: true } as never
    await flushPromises()
    expect(pinKeys(wrapper)).toEqual(['project', 'attachments', 'terminal', 'git', 'search'])
    // Each pin is an icon with its name as the accessible name, in every language.
    for (const pin of wrapper.findAll('[data-test="taskbar-pin"]')) {
      expect(pin.attributes('aria-label')).toBe(String(i18n.global.t(`gui.taskbar.pin_${pin.attributes('data-pin')}`)))
      expect(pin.find('path').attributes('d')).toBe(PIN_ICONS[pin.attributes('data-pin') as keyof typeof PIN_ICONS])
    }
  })
  it('hands project, attachments, terminal and Git to the Shell and moves the keyboard to the search box for search', async () => {
    flowTerminalUi.status = { enabled: true, allowed: true } as never
    const search = document.createElement('input')
    search.setAttribute('data-test', 'designer-search-input')
    document.body.appendChild(search)
    const wrapper = bar({ hasFlow: true })
    await flushPromises()
    for (const key of ['project', 'attachments', 'terminal', 'git']) await wrapper.get(`[data-pin="${key}"]`).trigger('click')
    expect(wrapper.emitted('pin')).toEqual([['project'], ['attachments'], ['terminal'], ['git']])
    await wrapper.get('[data-pin="search"]').trigger('click')
    expect(document.activeElement).toBe(search)
    expect(wrapper.emitted('pin')).toHaveLength(4)
  })
  it('dims the Flow buttons without an open Flow, keeps their name tip, and lights a button whose window is open', async () => {
    flowTerminalUi.status = { enabled: true, allowed: true } as never
    const wrapper = bar({ hasFlow: false })
    await flushPromises()
    for (const key of ['attachments', 'terminal', 'git']) {
      const pin = wrapper.get(`[data-pin="${key}"]`)
      expect(pin.attributes('aria-disabled')).toBe('true')
      await pin.trigger('click')
    }
    expect(wrapper.get('[data-pin="project"]').attributes('aria-disabled')).toBeUndefined()
    expect(wrapper.emitted('pin')).toBeUndefined()
    await wrapper.get('[data-pin="git"]').trigger('pointerenter')
    await flushPromises()
    expect(wrapper.get('[data-test="taskbar-tip"]').text()).toBe('Git')
    await wrapper.setProps({ hasFlow: true })
    flowAttachmentUi.currentOpen = true
    flowTerminalUi.currentOpen = true
    await flushPromises()
    expect(wrapper.get('[data-pin="attachments"]').classes()).toContain('is-on')
    expect(wrapper.get('[data-pin="terminal"]').classes()).toContain('is-on')
    expect(wrapper.get('[data-pin="git"]').classes()).not.toContain('is-on')
  })
  it('leaves room for the pins when counting how many windows fit, and drops the designer item on narrow bars', async () => {
    expect(taskbarCapacity(1100, 20, 'modern', 5)).toBeLessThan(taskbarCapacity(1100, 20, 'modern'))
    expect(taskbarCapacity(2000, 5, 'flowgate', 5)).toBe(5)
    const wrapper = bar({}, 420)
    await flushPromises()
    expect(wrapper.get('[data-test="designer-taskbar"]').classes()).toContain('is-compact')
    expect(wrapper.find('.designer-item').exists()).toBe(false)
    expect(pinKeys(wrapper)).toHaveLength(4)
  })
})

describe('telling minimized windows apart', () => {
  it('draws a different icon per window kind, from an explicit kind or from the kind colour', async () => {
    expect(modelessKindOf({ color: '#52b788' })).toBe('git')
    expect(modelessKindOf({ color: '#4BA3FF' })).toBe('document')
    expect(modelessKindOf({ color: '#4ba3ff', kind: 'note' })).toBe('note')
    expect(modelessKindOf({ color: 'var(--fg2-accent)' })).toBe('window')
    expect(new Set(Object.values(KIND_ICONS)).size).toBe(Object.keys(KIND_ICONS).length)
    const wrapper = bar()
    await flushPromises()
    await minimizedWindow({ title: 'Doc', kindColor: '#4ba3ff' })
    await minimizedWindow({ title: 'Git', kindColor: '#52b788' })
    await minimizedWindow({ title: 'Memo', kindColor: '#4ba3ff', kind: 'note' })
    const items = wrapper.findAll('.minimized-item')
    expect(items.map(item => item.attributes('data-kind'))).toEqual(['document', 'git', 'note'])
    expect(new Set(items.map(item => item.find('path').attributes('d'))).size).toBe(3)
    // One of each kind needs no mark.
    expect(wrapper.find('[data-test="taskbar-badge"]').exists()).toBe(false)
  })
  it('marks windows of the same kind with their document number, or their place among them', async () => {
    expect(documentNumberBadge('flowgate.v02test.0055.0012-CH')).toBe('12')
    expect(documentNumberBadge('plain')).toBeUndefined()
    const wrapper = bar()
    await flushPromises()
    await minimizedWindow({ title: 'Doc A', kindColor: '#4ba3ff', badge: '12' })
    await minimizedWindow({ title: 'Doc B', kindColor: '#4ba3ff', badge: '3' })
    await minimizedWindow({ title: 'Window A' })
    await minimizedWindow({ title: 'Window B' })
    expect(wrapper.findAll('[data-test="taskbar-badge"]').map(b => b.text())).toEqual(['12', '3', '1', '2'])
  })
  it('shows a minimized window name at once above its icon on hover and on keyboard focus', async () => {
    const wrapper = bar()
    await flushPromises()
    await minimizedWindow({ title: 'Requirements 0001-R', kindColor: '#4ba3ff' })
    const item = wrapper.get('.minimized-item')
    expect(item.attributes('title')).toBeUndefined()
    expect(item.attributes('aria-label')).toBe('Requirements 0001-R')
    await item.trigger('pointerenter')
    await flushPromises()
    expect(wrapper.get('[data-test="taskbar-tip"]').text()).toBe('Requirements 0001-R')
    await item.trigger('pointerleave')
    expect(wrapper.find('[data-test="taskbar-tip"]').exists()).toBe(false)
    await item.trigger('focus')
    await flushPromises()
    expect(wrapper.get('[data-test="taskbar-tip"]').text()).toBe('Requirements 0001-R')
    // Opening the window hides the tip.
    await item.trigger('click')
    expect(wrapper.find('[data-test="taskbar-tip"]').exists()).toBe(false)
  })
  it('keeps the visible name (and its title) in the FlowGate style, with no mark', async () => {
    style.value = 'flowgate'
    const wrapper = bar()
    await flushPromises()
    await minimizedWindow({ title: 'Window A' })
    await minimizedWindow({ title: 'Window B' })
    const item = wrapper.get('.minimized-item')
    expect(item.attributes('title')).toBe('Window A')
    expect(item.get('.item-text').text()).toBe('Window A')
    await item.trigger('pointerenter')
    expect(wrapper.find('[data-test="taskbar-tip"]').exists()).toBe(false)
  })
})
