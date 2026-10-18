import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { createDesignerMenuHost } from '@/features/designer/contextMenu/host'
import { openContextMenu } from '@/features/designer/contextMenu/entry'
import { menuPosition, submenuPosition } from '@/features/designer/contextMenu/position'
import { modelFor, targets, firstConnected } from './contextMenuFixtures.0060'

let wrapper: VueWrapper | null = null
const cleanup: (() => void)[] = []
afterEach(() => {
  wrapper?.unmount(); wrapper = null
  cleanup.splice(0).forEach((fn) => fn())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
const q = (selector: string) => document.querySelector<HTMLElement>(selector)!
const menu = () => q('[data-level="0"]')
const key = async (key: string) => {
  document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  await flushPromises()
}
async function setup(model = modelFor(targets[5])) {
  const trigger = document.createElement('button')
  document.body.append(trigger); trigger.focus()
  const host = createDesignerMenuHost()
  const i18n = createI18n({ legacy: false, locale: 'ko', fallbackLocale: 'en' })
  wrapper = mount(DesignerContextMenu, { props: { host }, attachTo: document.body, global: { plugins: [i18n] } })
  host.open(model, { x: 40, y: 50 })
  await flushPromises()
  return { host, trigger, i18n }
}
const button = (name: string) => q('[data-key="' + name + '"]')

describe('Designer common context menu (0060)', () => {
  it('renders icons, separators, danger, child arrows and localized labels', async () => {
    const { i18n } = await setup(modelFor(targets[3]))
    expect(menu().textContent).toContain('새 폴더')
    expect(menu().querySelectorAll('[role="separator"]')).toHaveLength(3)
    expect(button('folder.git').getAttribute('aria-haspopup')).toBe('menu')
    expect(button('folder.delete').classList.contains('danger')).toBe(true)
    expect(button('folder.open').querySelector('[aria-hidden]')?.textContent).toBe('↗')
    i18n.global.locale.value = 'en'
    await nextTick()
    expect(button('folder.open').textContent).toContain('Open')
    i18n.global.locale.value = 'ja'
    await nextTick()
    expect(button('folder.open').textContent).toContain('開く')
  })
  it('opens an all-disabled menu, consumes once, focuses first, rejects click/Enter/Space and owns Escape', async () => {
    const locked = modelFor(targets[9], firstConnected, { progressed: true })
    const { host, trigger } = await setup(locked)
    expect(button('edge.disconnect').getAttribute('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(button('edge.disconnect'))
    const bubble = vi.fn()
    document.addEventListener('keydown', bubble)
    cleanup.push(() => document.removeEventListener('keydown', bubble))
    button('edge.disconnect').click()
    await key('Enter'); await key(' ')
    expect(wrapper!.emitted('request')).toBeUndefined()
    expect(host.current.value).not.toBeNull()
    bubble.mockClear()
    await key('Escape')
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(bubble).not.toHaveBeenCalled()
    await key('Escape')
    expect(bubble).toHaveBeenCalledTimes(1)
    const ev = new MouseEvent('contextmenu', { cancelable: true })
    expect(openContextMenu(ev, host, locked)).toBe(true)
    expect(ev.defaultPrevented).toBe(true)
    expect(openContextMenu(ev, host, locked)).toBe(false)
  })
  it('focuses first enabled and arrows/Home/End visit disabled items, skipping separators', async () => {
    await setup(modelFor({ kind: 'tab', flowId: null, tabIds: ['f'] }))
    expect(document.activeElement).toBe(button('tab.closeAll'))
    await key('Home')
    expect(document.activeElement).toBe(button('tab.close'))
    await key('ArrowDown')
    expect(document.activeElement).toBe(button('tab.closeOthers'))
    await key('End')
    expect(document.activeElement).toBe(button('tab.closeAll'))
    await key('ArrowDown')
    expect(document.activeElement).toBe(button('tab.close'))
    await key('ArrowUp')
    expect(document.activeElement).toBe(button('tab.closeAll'))
  })
  it('opens submenus by hover/right/click, closes by left or another root item, and never requests a parent', async () => {
    await setup(modelFor(targets[3]))
    const parent = button('folder.git')
    parent.dispatchEvent(new MouseEvent('mouseenter'))
    await flushPromises()
    expect(q('[data-level="1"]')).not.toBeNull()
    expect(parent.getAttribute('aria-expanded')).toBe('true')
    button('folder.open').dispatchEvent(new MouseEvent('mouseenter'))
    await nextTick()
    expect(q('[data-level="1"]')).toBeNull()
    parent.focus()
    await key('ArrowRight')
    expect(document.activeElement).toBe(button('folder.git.changes'))
    await key('ArrowLeft')
    expect(q('[data-level="1"]')).toBeNull()
    expect(document.activeElement).toBe(parent)
    parent.click()
    await flushPromises()
    expect(wrapper!.emitted('request')).toBeUndefined()
    button('folder.git.history').click()
    await nextTick()
    expect(wrapper!.emitted('request')).toEqual([[{ command: 'folder.git.history', target: targets[3] }]])
  })
  it('does not open disabled parents even when all children remain visible definitions', async () => {
    await setup(modelFor(targets[3], undefined, { git: 'denied' }))
    const parent = button('folder.git')
    expect(parent.title).toBe('Git 읽기 권한 없음')
    parent.dispatchEvent(new MouseEvent('mouseenter')); parent.click(); parent.focus()
    await key('ArrowRight'); await key('Enter')
    expect(q('[data-level="1"]')).toBeNull()
    expect(wrapper!.emitted('request')).toBeUndefined()
  })
  it('selects a leaf exactly once with the opening snapshot and returns focus', async () => {
    const target = { kind: 'tab', flowId: 'f', tabIds: ['f', 'other'] } as const
    const { host, trigger } = await setup(modelFor(target))
    const oldButton = button('tab.close')
    const generation = host.current.value!.generation
    oldButton.click(); oldButton.click()
    await nextTick()
    expect(wrapper!.emitted('request')).toEqual([[{ command: 'tab.close', target }]])
    expect(host.select('tab.close', generation)).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
  it('selects a card child by Space with its exact input definition', async () => {
    const { host } = await setup(modelFor(targets[6], firstConnected))
    button('canvas.addCard').focus()
    await key('ArrowRight')
    await key('End')
    await key(' ')
    const request = (wrapper!.emitted('request')![0] as any[])[0]
    expect(request.command).toBe('canvas.addCard')
    expect(request.definition).toMatchObject({ itemId: 'confirm', nodeKind: 'approval', docType: null })
    expect(request.target).toEqual(targets[6])
    expect(host.current.value).toBeNull()
  })
  it('replaces one owned session and rejects stale selection and invalidation', async () => {
    const { host, trigger } = await setup()
    const old = host.current.value!.generation
    const replacement = host.open(modelFor(targets[2]), { x: 90, y: 100 })!
    expect(replacement).toBeGreaterThan(old)
    expect(host.select('tab.close', old)).toBeNull()
    expect(host.invalidate(old)).toBe(false)
    await flushPromises()
    expect(document.querySelectorAll('[data-level="0"]')).toHaveLength(1)
    expect(button('flow.rename')).not.toBeNull()
    expect(host.invalidate(replacement)).toBe(true)
    expect(document.activeElement).toBe(trigger)
  })
  it.each(['pointerdown', 'mousedown', 'contextmenu', 'scroll', 'resize', 'invalidate'])('closes and restores focus on %s', async (type) => {
    const { host, trigger } = await setup()
    if (type === 'resize') window.dispatchEvent(new Event(type))
    else if (type === 'invalidate') host.invalidate(host.current.value!.generation)
    else document.body.dispatchEvent(new Event(type, { bubbles: true }))
    await nextTick()
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
  it('keeps internal scroll and contextmenu inside, then restores fallback if trigger disappears', async () => {
    const { host, trigger } = await setup()
    menu().dispatchEvent(new Event('scroll', { bubbles: true }))
    const right = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    button('tab.close').dispatchEvent(right)
    expect(right.defaultPrevented).toBe(true)
    expect(host.current.value).not.toBeNull()
    const fallback = document.createElement('button')
    document.body.append(fallback)
    host.open(modelFor(targets[5]), { x: 1, y: 1 }, fallback)
    trigger.remove()
    host.close()
    expect(document.activeElement).toBe(fallback)
  })
  it('tears down listeners and closes on unmount without leaving Escape ownership', async () => {
    const { host, trigger } = await setup()
    wrapper!.unmount(); wrapper = null
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
    const bubble = vi.fn()
    document.addEventListener('keydown', bubble)
    cleanup.push(() => document.removeEventListener('keydown', bubble))
    await key('Escape')
    expect(bubble).toHaveBeenCalledTimes(1)
  })
  it('does not steal focus from a closed/replaced session during nextTick positioning', async () => {
    const { host, trigger } = await setup()
    host.open(modelFor(targets[3]), { x: 1, y: 1 })
    host.close()
    await flushPromises()
    expect(document.activeElement).toBe(trigger)
    expect(menu()).toBeNull()
  })
  it.each(['input', 'textarea', 'select', 'editable', 'editable-child', 'plaintext'])('preserves native menu in %s', async (kind) => {
    const { host } = await setup()
    host.close()
    const el = document.createElement(['input', 'textarea', 'select'].includes(kind) ? kind : 'div')
    if (kind.startsWith('editable') || kind === 'plaintext') el.setAttribute('contenteditable', kind === 'plaintext' ? 'plaintext-only' : 'true')
    if (kind === 'editable-child') el.append(document.createElement('span'))
    document.body.append(el)
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    el.addEventListener('contextmenu', (e) => openContextMenu(e, host, modelFor(targets[5])))
    ;(el.firstElementChild ?? el).dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(host.current.value).toBeNull()
  })
  it('does not consume empty/excluded targets or fall back from hidden multi to outer canvas', async () => {
    const { host } = await setup()
    const e = new MouseEvent('contextmenu', { cancelable: true })
    expect(openContextMenu(e, host, modelFor(targets[8], firstConnected))).toBe(false)
    expect(openContextMenu(e, host, modelFor(targets[6], firstConnected))).toBe(false)
    expect(e.defaultPrevented).toBe(false)
    expect(host.current.value).toBeNull()
    for (const _excluded of ['minimap', 'palette-blank']) {
      const event = new MouseEvent('contextmenu', { cancelable: true })
      expect(openContextMenu(event, host, null)).toBe(false)
      expect(event.defaultPrevented).toBe(false)
    }
  })
  it('measures panels and positions a submenu left when near the right boundary', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.dataset.key === 'folder.git') return { left: 800, right: 990, top: 80, width: 190, height: 30 } as DOMRect
      return { left: 0, right: 190, top: 0, width: 190, height: 240 } as DOMRect
    })
    const { host } = await setup(modelFor(targets[3]))
    host.open(modelFor(targets[3]), { x: window.innerWidth, y: window.innerHeight })
    await flushPromises()
    expect(parseFloat(menu().style.left)).toBe(window.innerWidth - 198)
    expect(parseFloat(menu().style.top)).toBe(window.innerHeight - 248)
    button('folder.git').click()
    await flushPromises()
    expect(parseFloat(q('[data-level="1"]').style.left)).toBe(606)
  })
  it.each([[0, 0], [1000, 0], [0, 800], [1000, 800], [-50, -60]])('clamps viewport corner %j', (x, y) => {
    const pos = menuPosition({ x, y }, { width: 190, height: 260 }, { width: 1000, height: 800 })
    expect(pos.x).toBeGreaterThanOrEqual(8); expect(pos.y).toBeGreaterThanOrEqual(8)
    expect(pos.x + 190).toBeLessThanOrEqual(992); expect(pos.y + 260).toBeLessThanOrEqual(792)
  })
  it('places the submenu on the right when it fits, and clamps the left edge too', () => {
    expect(submenuPosition({ left: 10, right: 200, top: 50 }, { width: 190, height: 260 }, { width: 1000, height: 800 })).toEqual({ x: 204, y: 50 })
    expect(submenuPosition({ left: 10, right: 200, top: 50 }, { width: 190, height: 260 }, { width: 300, height: 800 })).toEqual({ x: 8, y: 50 })
  })
})

