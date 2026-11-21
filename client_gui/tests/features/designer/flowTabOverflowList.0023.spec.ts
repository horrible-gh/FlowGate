/** the flow tab strip keeps its height when tabs overflow — no scrollbar, a wheel that scrolls
 *  sideways and, only while the tabs overflow, a list button that opens every open Flow (ui/Dropdown). */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = '' })

const TABS = [{ flowId: 'a', name: '서버' }, { flowId: 'b', name: '디자이너 관리' }, { flowId: 'c', name: '채팅' }]

function mountStrip(props: Record<string, unknown> = {}) {
  i18n.global.locale.value = 'ko'
  wrapper = mount(FlowTabStrip, {
    props: { tabs: TABS, activeFlowId: 'a', runStates: { c: 'running' }, ...props },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  return wrapper
}

/** jsdom has no layout: pin the strip's content width and the bar's width, then let the strip re-measure. */
async function setWidths(w: VueWrapper<any>, content: number, bar: number) {
  const strip = w.get('[data-test="designer-flow-tabs"]').element as HTMLElement
  const barEl = w.get('[data-test="designer-flow-tab-bar"]').element as HTMLElement
  Object.defineProperty(strip, 'scrollWidth', { configurable: true, get: () => content })
  Object.defineProperty(strip, 'clientWidth', { configurable: true, get: () => Math.min(content, bar) })
  Object.defineProperty(barEl, 'clientWidth', { configurable: true, get: () => bar })
  strip.dispatchEvent(new Event('scroll'))
  await nextTick(); await nextTick()
}

const listButton = (w: VueWrapper<any>) => w.find('[data-test="designer-flow-tab-list-button"]')
const listItems = () => [...document.body.querySelectorAll<HTMLElement>('[data-test="designer-flow-tab-list-item"]')]

describe('flow tab strip overflow (v02gui.0023)', () => {
  it('shows the list button only while the tabs overflow, outside the tablist', async () => {
    const w = mountStrip()
    await setWidths(w, 300, 760)
    expect(listButton(w).exists()).toBe(false)
    await setWidths(w, 900, 760)
    const button = listButton(w)
    expect(button.exists()).toBe(true)
    expect(button.attributes('aria-label')).toBe('열린 플로우 목록')
    expect(button.attributes('aria-haspopup')).toBe('menu')
    expect(w.get('[data-test="designer-flow-tabs"]').find('[data-test="designer-flow-tab-list-button"]').exists()).toBe(false)
    // Tabs that fit the whole bar hide it again (the button's own width never keeps it shown).
    await setWidths(w, 740, 760)
    expect(listButton(w).exists()).toBe(false)
  })

  it('lists every open Flow in tab order, marks the active one and activates the picked one', async () => {
    const w = mountStrip()
    await setWidths(w, 900, 300)
    await listButton(w).trigger('click')
    await nextTick(); await nextTick()
    expect(document.body.querySelector('[data-test="designer-flow-tab-list"]')?.textContent).toContain('열린 플로우 3개')
    const items = listItems()
    expect(items.map((item) => item.dataset.flowId)).toEqual(['a', 'b', 'c'])
    expect(items.map((item) => item.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false'])
    expect(items[2].querySelector('.flow-tab-run.is-running')).not.toBeNull()
    expect(document.activeElement).toBe(items[0])
    items[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    expect(document.activeElement).toBe(items[2])
    items[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    expect(document.activeElement).toBe(items[0])
    items[1].click()
    await nextTick()
    expect(w.emitted('activate')).toEqual([['b']])
    expect(listItems()).toHaveLength(0)
  })

  it('closes an open list when the tabs stop overflowing', async () => {
    const w = mountStrip()
    await setWidths(w, 900, 300)
    await listButton(w).trigger('click')
    await nextTick()
    expect(listItems()).toHaveLength(3)
    await setWidths(w, 200, 300)
    expect(listButton(w).exists()).toBe(false)
    expect(listItems()).toHaveLength(0)
  })

  it('turns a vertical wheel into a sideways scroll only while overflowing', async () => {
    const w = mountStrip()
    const strip = w.get('[data-test="designer-flow-tabs"]').element as HTMLElement
    let left = 0
    Object.defineProperty(strip, 'scrollLeft', { configurable: true, get: () => left, set: (v: number) => { left = v } })
    await setWidths(w, 300, 760)
    strip.dispatchEvent(new WheelEvent('wheel', { deltaY: 100, bubbles: true, cancelable: true }))
    expect(left).toBe(0)
    await setWidths(w, 900, 300)
    const event = new WheelEvent('wheel', { deltaY: 100, bubbles: true, cancelable: true })
    strip.dispatchEvent(event)
    expect(left).toBe(100)
    expect(event.defaultPrevented).toBe(true)
  })

  it('hides the strip scrollbar and keeps the 36px height on the bar', () => {
    const source = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    const block = (selector: string) => source.match(new RegExp(`\\n${selector.replace(/[.:-]/g, (c) => `\\${c}`)} \\{([^}]*)\\}`))?.[1] ?? ''
    expect(block('.flow-tab-bar')).toContain('height: 36px;')
    expect(block('.flow-tab-strip')).toContain('scrollbar-width: none;')
    expect(block('.flow-tab-strip')).not.toMatch(/\bheight:/)
    expect(block('.flow-tab-strip::-webkit-scrollbar')).toContain('display: none;')
    expect(block('.flow-tab-list-items')).toContain('max-height: min(60vh, 360px);')
  })
})
