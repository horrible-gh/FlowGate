/** flowgate.v02test.0088 — 탭 더블클릭 시 그 탭만 번호만 보기 (per tab, kept in this browser by flowId). */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'
import {
  FLOW_TAB_COMPACT_IDS_STORAGE_KEY,
  LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY,
  readCompactFlowIds,
  shortTabName,
  writeCompactFlowIds,
} from '@/features/designer/center/flowTabCompact'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
function clearStorage() {
  localStorage.removeItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY)
  localStorage.removeItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY)
}
beforeEach(clearStorage)
afterEach(() => { wrapper?.unmount(); wrapper = null; clearStorage() })

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
const compactIdsOf = (w: VueWrapper<any>) => w.findAll('[data-test="designer-flow-tab"].is-compact').map((el) => el.attributes('data-flow-id'))
const stored = () => localStorage.getItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY)

describe('flowTabCompact', () => {
  it('shortens a name to 6 letters with …, keeps a short one, and counts whole characters', () => {
    expect(shortTabName('아주 긴 새 플로우 이름')).toBe('아주 긴 새…')
    expect(shortTabName('새 플로우')).toBe('새 플로우')
    expect(shortTabName('abcdef')).toBe('abcdef')
    expect(shortTabName('𠮷𠮷𠮷𠮷𠮷𠮷𠮷')).toBe('𠮷𠮷𠮷𠮷𠮷𠮷…')
  })

  it('remembers the folded flowIds in localStorage as a JSON array', () => {
    expect(readCompactFlowIds()).toEqual([])
    writeCompactFlowIds([B, L, B])
    expect(JSON.parse(stored()!)).toEqual([B, L])
    expect(readCompactFlowIds()).toEqual([B, L])
    writeCompactFlowIds([])
    expect(stored()).toBeNull()
    expect(readCompactFlowIds()).toEqual([])
  })

  it('reads a damaged value as none folded', () => {
    localStorage.setItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY, '{not json')
    expect(readCompactFlowIds()).toEqual([])
    localStorage.setItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY, '"x"')
    expect(readCompactFlowIds()).toEqual([])
    localStorage.setItem(FLOW_TAB_COMPACT_IDS_STORAGE_KEY, JSON.stringify([B, 3, null, '', B]))
    expect(readCompactFlowIds()).toEqual([B])
  })

  it('does not read the old strip-wide key and removes it on load', () => {
    localStorage.setItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY, '1')
    expect(readCompactFlowIds()).toEqual([])
    expect(localStorage.getItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY)).toBeNull()
  })
})

describe('Flow tab strip — 번호만 보기 per tab', () => {
  it('a double click folds that tab only; the others stay; again unfolds that tab only', async () => {
    const w = mountStrip()
    expect(compactIdsOf(w)).toEqual([])
    await buttonOf(w, B).trigger('dblclick')
    expect(compactIdsOf(w)).toEqual([B])
    expect(buttonOf(w, A).text()).toMatch(/^F37\s*남은 편의성$/)
    expect(buttonOf(w, L).text()).toBe('아주 긴 새 플로우 이름')
    // the strip itself carries no strip-wide mode any more
    expect(strip(w).classes()).not.toContain('is-compact')
    expect(strip(w).attributes('data-compact')).toBeUndefined()
    await buttonOf(w, L).trigger('dblclick')
    expect(compactIdsOf(w)).toEqual([B, L])
    await buttonOf(w, B).trigger('dblclick')
    expect(compactIdsOf(w)).toEqual([L])
    expect(buttonOf(w, B).text()).toMatch(/^F88\s*탭 공간 절약$/)
  })

  it('a folded tab shows marks + number (or the first letters without a number); the full name stays for screen readers', async () => {
    const w = mountStrip({ runStates: { [B]: 'running' }, chatStates: { [B]: true } })
    await buttonOf(w, B).trigger('dblclick')
    await buttonOf(w, L).trigger('dblclick')
    const b = buttonOf(w, B)
    const kids = [...b.element.children].map((el) => el.getAttribute('data-test'))
    expect(kids).toEqual(['designer-flow-tab-run', 'designer-flow-tab-chat', 'designer-flow-tab-no', 'designer-flow-tab-name-hidden'])
    expect(b.get('[data-test="designer-flow-tab-no"]').text()).toBe('F88')
    expect(b.get('[data-test="designer-flow-tab-name-hidden"]').text()).toBe('탭 공간 절약')
    expect(b.attributes('title')).toBe('F88 · 탭 공간 절약')
    const l = buttonOf(w, L)
    expect(l.find('[data-test="designer-flow-tab-no"]').exists()).toBe(false)
    expect(l.get('[data-test="designer-flow-tab-short"]').text()).toBe('아주 긴 새…')
    expect(l.get('[data-test="designer-flow-tab-short"]').attributes('aria-hidden')).toBe('true')
    expect(l.get('[data-test="designer-flow-tab-name-hidden"]').text()).toBe('아주 긴 새 플로우 이름')
    expect(l.attributes('title')).toBe('아주 긴 새 플로우 이름')
    // the unfolded active tab keeps its name
    expect(buttonOf(w, A).find('[data-test="designer-flow-tab-name-hidden"]').exists()).toBe(false)
  })

  it('a folded tab stays folded while active (the active underline tells it apart); others do not change', async () => {
    const w = mountStrip()
    await buttonOf(w, A).trigger('dblclick')
    expect(tabOf(w, A).classes()).toEqual(expect.arrayContaining(['is-active', 'is-compact']))
    expect(buttonOf(w, A).get('[data-test="designer-flow-tab-no"]').text()).toBe('F37')
    expect(buttonOf(w, A).get('[data-test="designer-flow-tab-name-hidden"]').text()).toBe('남은 편의성')
    expect(buttonOf(w, A).attributes('title')).toBe('F37 · 남은 편의성')
    await buttonOf(w, B).trigger('click')
    expect(w.emitted('activate')?.at(-1)).toEqual([B])
    await w.setProps({ activeFlowId: B })
    expect(compactIdsOf(w)).toEqual([A])
    expect(buttonOf(w, B).text()).toMatch(/^F88\s*탭 공간 절약$/)
  })

  it('keeps the folded tabs across a reload (a new mount reads them back) and ignores the old key', async () => {
    localStorage.setItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY, '1')
    let w = mountStrip()
    expect(compactIdsOf(w)).toEqual([])
    expect(localStorage.getItem(LEGACY_FLOW_TAB_COMPACT_STORAGE_KEY)).toBeNull()
    await buttonOf(w, B).trigger('dblclick')
    expect(JSON.parse(stored()!)).toEqual([B])
    w.unmount(); wrapper = null
    w = mountStrip()
    expect(compactIdsOf(w)).toEqual([B])
    await buttonOf(w, B).trigger('dblclick')
    expect(stored()).toBeNull()
  })

  it('a double click on × does not fold; clicks still activate and × still closes', async () => {
    const w = mountStrip()
    const close = tabOf(w, B).get('[data-test="designer-flow-tab-close"]')
    await close.trigger('dblclick')
    expect(compactIdsOf(w)).toEqual([])
    await close.trigger('click')
    expect(w.emitted('close')?.at(-1)).toEqual([B])
    await buttonOf(w, B).trigger('click')
    expect(w.emitted('activate')?.at(-1)).toEqual([B])
  })

  it('keeps × on the active tab and shows it on other folded tabs only while hovered (CSS)', async () => {
    const w = mountStrip()
    await buttonOf(w, A).trigger('dblclick')
    await buttonOf(w, B).trigger('dblclick')
    expect(w.findAll('[data-test="designer-flow-tab"] .flow-tab-close')).toHaveLength(3)
    const src = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    expect(src).toMatch(/\.flow-tab\.is-compact:not\(\.is-active\):not\(:hover\):not\(:focus-within\) \.flow-tab-close \{\s*display: none;/)
    expect(src).toMatch(/\.flow-tab-name-hidden \{[^}]*position: absolute;[^}]*clip: rect\(0 0 0 0\);/)
    expect(src).not.toMatch(/\.flow-tab-strip\.is-compact/)
  })
})
