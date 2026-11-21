/** archived tabs and the ended run / chat marks of the Flow tab strip. */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null })

const tabs = [{ flowId: 'a', name: 'A' }, { flowId: 'b', name: 'B' }, { flowId: 'c', name: 'C' }]
function mountStrip(props: Record<string, unknown>) {
  i18n.global.locale.value = 'ko'
  wrapper = mount(FlowTabStrip, { props: { tabs, activeFlowId: 'a', ...props }, global: { plugins: [i18n] }, attachTo: document.body })
  return wrapper
}
const tabOf = (w: VueWrapper<any>, id: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${id}"]`)

describe('archived Flow tab', () => {
  it('is dimmed with the archive icon and its "아카이브됨" tooltip; the active one too', () => {
    const w = mountStrip({ archived: { a: true, b: true, c: false } })
    for (const id of ['a', 'b']) {
      const tab = tabOf(w, id)
      expect(tab.classes()).toContain('is-archived')
      const icon = tab.get('[data-test="designer-flow-tab-archived"]')
      expect(icon.attributes('title')).toBe('아카이브됨')
      expect(icon.find('svg[data-icon="archive"]').exists()).toBe(true)
    }
    expect(tabOf(w, 'a').classes()).toContain('is-active')
    expect(tabOf(w, 'c').classes()).not.toContain('is-archived')
    expect(tabOf(w, 'c').find('[data-test="designer-flow-tab-archived"]').exists()).toBe(false)
    // The tab still closes / activates as before.
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-close"]').exists()).toBe(true)
  })

  it('returns to normal when restored (no reload)', async () => {
    const w = mountStrip({ archived: { b: true } })
    await w.setProps({ archived: { b: false } })
    expect(tabOf(w, 'b').classes()).not.toContain('is-archived')
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-archived"]').exists()).toBe(false)
  })

  it('uses the Explorer\'s 0.5 and 0.75 for the active tab', () => {
    const src = readFileSync(resolve(__dirname, '../../../src/features/designer/center/FlowTabStrip.vue'), 'utf8')
    expect(src).toMatch(/\.flow-tab\.is-archived \{\s*opacity: 0\.5;/)
    expect(src).toMatch(/\.flow-tab\.is-archived\.is-active \{\s*opacity: 0\.75;/)
  })
})

describe('ended run mark', () => {
  it.each([
    ['success', 'completed', '실행 완료', 'circle[fill="none"]'],
    ['warning', 'needs_intervention', '개입 필요', 'circle[fill="currentColor"]'],
    ['danger', 'error', '오류로 멈춤', 'circle[fill="currentColor"]'],
    ['neutral', 'stopped', '중지됨', 'circle[fill="none"]'],
  ])('%s (%s) shows its icon and result name', (tone, reason, label, glyph) => {
    const w = mountStrip({ runEnds: { b: { tone, reason } } })
    const mark = tabOf(w, 'b').get('[data-test="designer-flow-tab-run-end"]')
    expect(mark.classes()).toContain(`is-${tone}`)
    expect(mark.attributes('title')).toBe(label)
    expect(mark.find(glyph).exists()).toBe(true)
  })

  it('gives way to a running mark and names an unknown reason "실행 끝남"', async () => {
    const w = mountStrip({ runEnds: { b: { tone: 'neutral', reason: 'whatever' } } })
    expect(tabOf(w, 'b').get('[data-test="designer-flow-tab-run-end"]').attributes('title')).toBe('실행 끝남')
    await w.setProps({ runStates: { b: 'running' } })
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-run-end"]').exists()).toBe(false)
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-run"]').exists()).toBe(true)
  })
})

describe('ended chat mark', () => {
  it('keeps the bubble with a blue (done) or orange (failed) dot, and yields to an answering chat', async () => {
    const w = mountStrip({ chatEnds: { b: 'done', c: 'failed' } })
    const done = tabOf(w, 'b').get('[data-test="designer-flow-tab-chat-end"]')
    expect(done.classes()).toContain('is-done')
    expect(done.attributes('title')).toBe('채팅 답변이 끝났습니다')
    expect(done.find('svg.chat-bubble-icon').exists()).toBe(true)
    const failed = tabOf(w, 'c').get('[data-test="designer-flow-tab-chat-end"]')
    expect(failed.classes()).toContain('is-failed')
    expect(failed.attributes('title')).toBe('채팅 답변이 끝나지 못했습니다')
    await w.setProps({ chatStates: { b: true } })
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-chat-end"]').exists()).toBe(false)
    expect(tabOf(w, 'b').find('[data-test="designer-flow-tab-chat"]').exists()).toBe(true)
  })
})

describe('overflowing tabs', () => {
  function overflowGeometry(w: VueWrapper<any>, visible: string[]) {
    const strip = w.get('[data-test="designer-flow-tabs"]').element as HTMLElement
    const bar = w.get('[data-test="designer-flow-tab-bar"]').element as HTMLElement
    Object.defineProperty(strip, 'scrollWidth', { configurable: true, value: 600 })
    Object.defineProperty(bar, 'clientWidth', { configurable: true, value: 200 })
    strip.getBoundingClientRect = () => ({ left: 0, right: 200, top: 0, bottom: 36, width: 200, height: 36, x: 0, y: 0, toJSON() {} })
    tabs.forEach((tab, i) => {
      const el = w.get(`[data-test="designer-flow-tab"][data-flow-id="${tab.flowId}"]`).element as HTMLElement
      const left = visible.includes(tab.flowId) ? i * 60 : 300 + i * 60
      el.getBoundingClientRect = () => ({ left, right: left + 60, top: 0, bottom: 36, width: 60, height: 36, x: left, y: 0, toJSON() {} })
    })
  }

  it('puts a dot on the list button while a hidden tab holds an ended mark, and shows the marks in the list', async () => {
    const w = mountStrip({ runEnds: { c: { tone: 'success', reason: 'completed' } }, chatEnds: { c: 'done' }, archived: { b: true } })
    overflowGeometry(w, ['a', 'b'])
    w.get('[data-test="designer-flow-tabs"]').trigger('scroll')
    await w.vm.$nextTick()
    const button = w.get('[data-test="designer-flow-tab-list-button"]')
    expect(button.attributes('data-has-mark')).toBe('true')
    expect(button.classes()).toContain('has-mark')
    await button.trigger('click')
    await w.vm.$nextTick()
    const item = document.querySelector('[data-test="designer-flow-tab-list-item"][data-flow-id="c"]')!
    expect(item.querySelector('[data-test="designer-flow-tab-list-run-end"]')?.getAttribute('data-tone')).toBe('success')
    expect(item.querySelector('[data-test="designer-flow-tab-list-chat-end"]')?.getAttribute('data-chat-end')).toBe('done')
    const archivedItem = document.querySelector('[data-test="designer-flow-tab-list-item"][data-flow-id="b"]')!
    expect(archivedItem.classList.contains('is-archived')).toBe(true)
  })

  it('has no dot when the marked tab is in view', async () => {
    const w = mountStrip({ chatEnds: { b: 'done' } })
    overflowGeometry(w, ['a', 'b'])
    w.get('[data-test="designer-flow-tabs"]').trigger('scroll')
    await w.vm.$nextTick()
    expect(w.get('[data-test="designer-flow-tab-list-button"]').attributes('data-has-mark')).toBeUndefined()
  })
})
