/** flowgate.v02test.0088 — 탭 더블클릭 시 번호만 보기 (the whole Flow tab strip, kept in this browser). */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'
import { FLOW_TAB_COMPACT_STORAGE_KEY, readFlowTabCompact, shortTabName, writeFlowTabCompact } from '@/features/designer/center/flowTabCompact'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
beforeEach(() => localStorage.removeItem(FLOW_TAB_COMPACT_STORAGE_KEY))
afterEach(() => { wrapper?.unmount(); wrapper = null; localStorage.removeItem(FLOW_TAB_COMPACT_STORAGE_KEY) })

const A = 'flowgate.v02test.0037'
const B = 'flowgate.v02test.0088'
const L = 'local-draft'
const tabs = [{ flowId: A, name: '남은 편의성' }, { flowId: B, name: '탭 공간 절약' }, { flowId: L, name: '아주 긴 새 플로우 이름' }]
function mountStrip(props: Record<string, unknown> = {}) {
  i18n.global.locale.value = 'ko'
  wrapper = mount(FlowTabStrip, { props: { tabs, activeFlowId: A, ...props }, global: { plugins: [i18n] }, attachTo: document.body })
  return wrapper
}
const tabOf = (w: VueWrapper<any>, id: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${id}"]`)
const buttonOf = (w: VueWrapper<any>, id: string) => tabOf(w, id).get('[data-test="designer-flow-tab-button"]')
const strip = (w: VueWrapper<any>) => w.get('[data-test="designer-flow-tabs"]')

describe('flowTabCompact', () => {
  it('shortens a name to 6 letters with …, keeps a short one, and counts whole characters', () => {
    expect(shortTabName('아주 긴 새 플로우 이름')).toBe('아주 긴 새…')
    expect(shortTabName('새 플로우')).toBe('새 플로우')
    expect(shortTabName('abcdef')).toBe('abcdef')
    expect(shortTabName('𠮷𠮷𠮷𠮷𠮷𠮷𠮷')).toBe('𠮷𠮷𠮷𠮷𠮷𠮷…')
  })

  it('remembers the mode in localStorage', () => {
    expect(readFlowTabCompact()).toBe(false)
    writeFlowTabCompact(true)
    expect(localStorage.getItem(FLOW_TAB_COMPACT_STORAGE_KEY)).toBe('1')
    expect(readFlowTabCompact()).toBe(true)
    writeFlowTabCompact(false)
    expect(readFlowTabCompact()).toBe(false)
  })
})

describe('Flow tab strip — 번호만 보기', () => {
  it('a double click on any tab switches the whole strip, and again switches it back', async () => {
    const w = mountStrip()
    expect(strip(w).classes()).not.toContain('is-compact')
    await buttonOf(w, B).trigger('dblclick')
    expect(strip(w).classes()).toContain('is-compact')
    expect(strip(w).attributes('data-compact')).toBe('true')
    // every inactive tab is short, not only the one clicked
    expect(tabOf(w, B).classes()).toContain('is-compact')
    expect(tabOf(w, L).classes()).toContain('is-compact')
    expect(tabOf(w, A).classes()).not.toContain('is-compact')
    await buttonOf(w, L).trigger('dblclick')
    expect(strip(w).classes()).not.toContain('is-compact')
    expect(w.findAll('[data-test="designer-flow-tab"].is-compact')).toHaveLength(0)
    expect(buttonOf(w, B).text()).toMatch(/^F88\s*탭 공간 절약$/)
  })

  it('inactive tabs show the number only, the active tab keeps its name, a Flow without a number its first letters', async () => {
    const w = mountStrip({ runStates: { [B]: 'running' }, chatStates: { [B]: true } })
    await buttonOf(w, A).trigger('dblclick')
    // active: number + name, as before
    expect(buttonOf(w, A).text()).toMatch(/^F37\s*남은 편의성$/)
    expect(buttonOf(w, A).find('[data-test="designer-flow-tab-name-hidden"]').exists()).toBe(false)
    // inactive numbered: marks kept before the number; the name is only visually hidden (screen readers still get it)
    const b = buttonOf(w, B)
    const kids = [...b.element.children].map((el) => el.getAttribute('data-test'))
    expect(kids).toEqual(['designer-flow-tab-run', 'designer-flow-tab-chat', 'designer-flow-tab-no', 'designer-flow-tab-name-hidden'])
    expect(b.get('[data-test="designer-flow-tab-no"]').text()).toBe('F88')
    expect(b.get('[data-test="designer-flow-tab-name-hidden"]').text()).toBe('탭 공간 절약')
    expect(b.attributes('title')).toBe('F88 · 탭 공간 절약')
    // no number: a short name on screen (hidden from screen readers), the full name for them
    const l = buttonOf(w, L)
    expect(l.find('[data-test="designer-flow-tab-no"]').exists()).toBe(false)
    expect(l.get('[data-test="designer-flow-tab-short"]').text()).toBe('아주 긴 새…')
    expect(l.get('[data-test="designer-flow-tab-short"]').attributes('aria-hidden')).toBe('true')
    expect(l.get('[data-test="designer-flow-tab-name-hidden"]').text()).toBe('아주 긴 새 플로우 이름')
    expect(l.attributes('title')).toBe('아주 긴 새 플로우 이름')
  })

  it('follows the active tab: the newly active one shows its name, the previous one shrinks', async () => {
    const w = mountStrip()
    await buttonOf(w, A).trigger('dblclick')
    await buttonOf(w, B).trigger('click')
    expect(w.emitted('activate')?.at(-1)).toEqual([B])
    await w.setProps({ activeFlowId: B })
    expect(tabOf(w, B).classes()).not.toContain('is-compact')
    expect(buttonOf(w, B).text()).toMatch(/^F88\s*탭 공간 절약$/)
    expect(tabOf(w, A).classes()).toContain('is-compact')
    expect(strip(w).classes()).toContain('is-compact')
  })

  it('keeps the mode across a reload (a new mount reads it back)', async () => {
    let w = mountStrip()
    await buttonOf(w, B).trigger('dblclick')
    expect(localStorage.getItem(FLOW_TAB_COMPACT_STORAGE_KEY)).toBe('1')
    w.unmount(); wrapper = null
    w = mountStrip()
    expect(strip(w).classes()).toContain('is-compact')
    await buttonOf(w, B).trigger('dblclick')
    expect(localStorage.getItem(FLOW_TAB_COMPACT_STORAGE_KEY)).toBeNull()
  })

  it('a double click on × does not switch the mode; clicks still activate and × still closes', async () => {
    const w = mountStrip()
    const close = tabOf(w, B).get('[data-test="designer-flow-tab-close"]')
    await close.trigger('dblclick')
    expect(strip(w).classes()).not.toContain('is-compact')
    await close.trigger('click')
    expect(w.emitted('close')?.at(-1)).toEqual([B])
    await buttonOf(w, B).trigger('click')
    expect(w.emitted('activate')?.at(-1)).toEqual([B])
  })

  it('keeps × on the active tab and shows it on other tabs only while hovered (CSS)', async () => {
    const w = mountStrip()
    await buttonOf(w, A).trigger('dblclick')
    expect(w.findAll('[data-test="designer-flow-tab"] .flow-tab-close')).toHaveLength(3)
    const src = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    expect(src).toMatch(/\.flow-tab\.is-compact:not\(:hover\):not\(:focus-within\) \.flow-tab-close \{\s*display: none;/)
    expect(src).toMatch(/\.flow-tab-name-hidden \{[^}]*position: absolute;[^}]*clip: rect\(0 0 0 0\);/)
  })
})
