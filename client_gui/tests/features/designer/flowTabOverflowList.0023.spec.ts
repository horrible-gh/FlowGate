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

  it.each([0, 2])('closes before a capture handler stops propagation (button %s), preserving the outside action', async (button) => {
    const w = mountStrip()
    await setWidths(w, 900, 300)
    await listButton(w).trigger('click')
    expect(listItems()).toHaveLength(3)
    const card = document.createElement('button')
    document.body.appendChild(card)
    let selected = false
    card.addEventListener('pointerdown', (event) => {
      selected = true
      event.stopImmediatePropagation()
    }, true)
    const event = new MouseEvent('pointerdown', { button, bubbles: true, cancelable: true })
    card.dispatchEvent(event)
    await nextTick()
    expect(listItems()).toHaveLength(0)
    expect(selected).toBe(true)
    expect(event.defaultPrevented).toBe(false)
  })

  it('does not close on a trigger pointerdown or reopen on its following click', async () => {
    const w = mountStrip()
    await setWidths(w, 900, 300)
    await listButton(w).trigger('click')
    await listButton(w).trigger('pointerdown')
    expect(listItems()).toHaveLength(3)
    await listButton(w).trigger('click')
    expect(listItems()).toHaveLength(0)
  })

  it('keeps menu pointerdown inside and activates the selected flow on click', async () => {
    const w = mountStrip()
    await setWidths(w, 900, 300)
    await listButton(w).trigger('click')
    const item = listItems()[1]
    item.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await nextTick()
    expect(listItems()).toHaveLength(3)
    item.click()
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

  /** flowgate.v02test.0100: the end tab's × hid behind ▾ because only the name button was scrolled into view. */
  it('scrolls the whole active tab (with its ×) into view, not only its name button, while overflowing', async () => {
    const had = Object.prototype.hasOwnProperty.call(Element.prototype, 'scrollIntoView')
    const original = Element.prototype.scrollIntoView
    const scrolled: Array<{ el: Element; options: unknown }> = []
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value(this: Element, options?: unknown) { scrolled.push({ el: this, options }) },
    })
    try {
      const w = mountStrip()
      await setWidths(w, 900, 300)
      await nextTick()
      scrolled.length = 0
      await w.setProps({ activeFlowId: 'c' })
      await nextTick(); await nextTick()
      const tab = w.get('[data-test="designer-flow-tab"][data-flow-id="c"]').element
      const button = tab.querySelector('[data-test="designer-flow-tab-button"]')
      const close = tab.querySelector('[data-test="designer-flow-tab-close"]')
      expect(close).not.toBeNull()
      expect(scrolled.length).toBeGreaterThan(0)
      const last = scrolled.at(-1)!
      expect(last.el).toBe(tab)
      expect(last.el).not.toBe(button)
      expect(last.el.contains(close)).toBe(true)
      expect(last.options).toEqual({ block: 'nearest', inline: 'nearest' })
    } finally {
      if (had) Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, writable: true, value: original })
      else delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView
    }
  })

  it('keeps the end tab off the list button edge with scroll-padding-inline on the strip', () => {
    const source = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    const block = (selector: string) => source.match(new RegExp(`\\n${selector.replace(/[.:-]/g, (c) => `\\${c}`)} \\{([^}]*)\\}`))?.[1] ?? ''
    expect(block('.flow-tab-strip')).toContain('scroll-padding-inline: 6px;')
    expect(block('.flow-tab-strip')).toContain('padding: 0 6px;')
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

/** flowgate.v02test.0066 #36-1: a tab's merge conflict — 병합 충돌, or AI 해결 중 · n/m — on the tab, in the list and as the list button's dot. */
describe('flow tab conflict / AI resolving marks (v02test.0066 #36-1)', () => {
  const tabMark = (w: VueWrapper<any>, id: string) =>
    w.get(`[data-test="designer-flow-tab"][data-flow-id="${id}"]`).find('[data-test="designer-flow-tab-conflict"]')

  /** Put tab `id` outside the strip's view (jsdom has no layout), then let the strip re-measure. */
  async function hideTab(w: VueWrapper<any>, id: string) {
    const strip = w.get('[data-test="designer-flow-tabs"]').element as HTMLElement
    strip.getBoundingClientRect = () => ({ left: 0, right: 300, top: 0, bottom: 36, width: 300, height: 36, x: 0, y: 0, toJSON: () => ({}) })
    const tab = w.get(`[data-test="designer-flow-tab"][data-flow-id="${id}"]`).element as HTMLElement
    tab.getBoundingClientRect = () => ({ left: 420, right: 560, top: 0, bottom: 36, width: 140, height: 36, x: 420, y: 0, toJSON: () => ({}) })
    strip.dispatchEvent(new Event('scroll'))
    await nextTick(); await nextTick()
  }

  it('shows 병합 충돌 for a conflict and AI 해결 중 · n/m while an AI resolves it, in the same 14px mark', async () => {
    const w = mountStrip({ conflicts: { a: true, b: { state: 'conflict' }, c: { state: 'resolving', done: 1, total: 46 } } })
    for (const id of ['a', 'b']) {
      expect(tabMark(w, id).attributes('title')).toBe('병합 충돌')
      expect(tabMark(w, id).attributes('aria-label')).toBe('병합 충돌')
      expect(tabMark(w, id).attributes('data-conflict-state')).toBe('conflict')
      expect(tabMark(w, id).classes()).not.toContain('is-resolving')
    }
    expect(tabMark(w, 'c').attributes('title')).toBe('AI 해결 중 · 1/46')
    expect(tabMark(w, 'c').attributes('aria-label')).toBe('AI 해결 중 · 1/46')
    expect(tabMark(w, 'c').attributes('data-conflict-state')).toBe('resolving')
    expect(tabMark(w, 'c').classes()).toEqual(expect.arrayContaining(['flow-tab-conflict', 'is-resolving']))
    // The same glyph in both states.
    expect(tabMark(w, 'c').find('svg path').attributes('d')).toBe(tabMark(w, 'a').find('svg path').attributes('d'))
  })

  it('follows the state: conflict → AI resolving (count moving) → conflict again (stopped) → gone (resolved)', async () => {
    const w = mountStrip({ conflicts: { b: true } })
    expect(tabMark(w, 'b').attributes('title')).toBe('병합 충돌')
    await w.setProps({ conflicts: { b: { state: 'resolving', done: 0, total: 46 } } })
    expect(tabMark(w, 'b').attributes('title')).toBe('AI 해결 중 · 0/46')
    await w.setProps({ conflicts: { b: { state: 'resolving', done: 12, total: 46 } } })
    expect(tabMark(w, 'b').attributes('title')).toBe('AI 해결 중 · 12/46')
    await w.setProps({ conflicts: { b: { state: 'conflict' } } })
    expect(tabMark(w, 'b').attributes('title')).toBe('병합 충돌')
    expect(tabMark(w, 'b').classes()).not.toContain('is-resolving')
    await w.setProps({ conflicts: {} })
    expect(tabMark(w, 'b').exists()).toBe(false)
  })

  it('says AI 해결 중 without numbers while the server gave no count', () => {
    const w = mountStrip({ conflicts: { b: { state: 'resolving', done: null, total: null }, c: { state: 'resolving', done: 0, total: 0 } } })
    expect(tabMark(w, 'b').attributes('title')).toBe('AI 해결 중')
    expect(tabMark(w, 'c').attributes('title')).toBe('AI 해결 중')
  })

  it.each([
    ['ko', '병합 충돌', 'AI 해결 중 · 3/7'],
    ['en', 'Merge conflict', 'AI resolving · 3/7'],
    ['ja', 'マージ競合', 'AIが解決中 · 3/7'],
  ])('has both tooltips in %s', async (locale, conflict, resolving) => {
    const w = mountStrip({ conflicts: { b: true, c: { state: 'resolving', done: 3, total: 7 } } })
    i18n.global.locale.value = locale as 'ko'
    await nextTick()
    expect(tabMark(w, 'b').attributes('title')).toBe(conflict)
    expect(tabMark(w, 'c').attributes('title')).toBe(resolving)
    i18n.global.locale.value = 'ko'
  })

  it.each([
    ['a conflict', true],
    ['an AI resolving it', { state: 'resolving', done: 1, total: 46 }],
  ])('puts the dot on the list button for a hidden tab with %s, and the same mark in the list', async (_name, mark) => {
    const w = mountStrip({ conflicts: { c: mark } })
    await setWidths(w, 900, 300)
    expect(listButton(w).attributes('data-has-mark')).toBeUndefined()
    await hideTab(w, 'c')
    expect(listButton(w).attributes('data-has-mark')).toBe('true')
    expect(listButton(w).attributes('aria-label')).toBe('열린 플로우 목록 — 가려진 탭에 확인할 표시가 있습니다')
    await listButton(w).trigger('click')
    await nextTick(); await nextTick()
    const inList = listItems()[2].querySelector<HTMLElement>('[data-test="designer-flow-tab-list-conflict"]')
    expect(inList).not.toBeNull()
    expect(inList!.getAttribute('title')).toBe(tabMark(w, 'c').attributes('title'))
    expect(inList!.classList.contains('is-resolving')).toBe(mark !== true)
    expect(listItems()[0].querySelector('[data-test="designer-flow-tab-list-conflict"]')).toBeNull()
    // The mark gone (resolved): the dot goes too.
    await w.setProps({ conflicts: {} })
    await nextTick(); await nextTick()
    expect(listButton(w).attributes('data-has-mark')).toBeUndefined()
  })

  it('keeps the tab name beside run, conflict and chat marks together (fixed-width marks, the name ellipsizes last)', () => {
    const w = mountStrip({ runStates: { b: 'running' }, chatStates: { b: true }, conflicts: { b: { state: 'resolving', done: 1, total: 46 } } })
    const button = w.get('[data-test="designer-flow-tab"][data-flow-id="b"] [data-test="designer-flow-tab-button"]')
    expect(button.find('.flow-tab-run').exists()).toBe(true)
    expect(button.find('.flow-tab-conflict.is-resolving').exists()).toBe(true)
    expect(button.find('.flow-tab-chat').exists()).toBe(true)
    expect(button.text()).toBe('디자이너 관리')
    const source = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    const block = (selector: string) => source.match(new RegExp(`\\n${selector.replace(/[.:-]/g, (c) => `\\${c}`)} \\{([^}]*)\\}`))?.[1] ?? ''
    // Conflict and AI 해결 중 share one 14px box (the ring is drawn inside it), so the state change never moves the name.
    expect(block('.flow-tab-conflict')).toContain('width: 14px;')
    expect(block('.flow-tab-conflict')).toContain('flex: none;')
    expect(block('.flow-tab-conflict.is-resolving::after')).toContain('inset: 0;')
    expect(block('.flow-tab-conflict.is-resolving::after')).toContain('animation: flow-tab-spin')
    expect(block('.flow-tab-conflict.is-resolving::after')).not.toMatch(/\bwidth:|\bmargin/)
  })
})
