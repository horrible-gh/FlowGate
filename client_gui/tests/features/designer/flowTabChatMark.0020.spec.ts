/** flowgate.v02gui.0020: the flow tab's "chat AI responding" mark is the right dock's '대화' icon at 14px, after the run mark. */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'

registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null })

const bubblePath = (root: Element | null | undefined) => root?.querySelector('svg.chat-bubble-icon path')?.getAttribute('d')

describe('flow tab chat mark', () => {
  it.each([['ko', '채팅 AI 응답 중'], ['en', 'Chat AI responding'], ['ja', 'チャットAI応答中']])('shows the shared bubble at 14px (%s)', (locale, label) => {
    i18n.global.locale.value = locale
    wrapper = mount(FlowTabStrip, {
      props: { tabs: [{ flowId: 'a', name: '설정불일치' }, { flowId: 'b', name: '서버' }], activeFlowId: 'a',
        runStates: { a: 'running' }, chatStates: { a: true } },
      global: { plugins: [i18n] },
    })
    const [first, second] = wrapper.findAll('[data-test="designer-flow-tab-button"]')
    const mark = first.get('[data-test="designer-flow-tab-chat"]')
    expect(mark.attributes('role')).toBe('img')
    expect(mark.attributes('aria-label')).toBe(label)
    expect(mark.attributes('title')).toBe(label)
    const svg = mark.get('svg.chat-bubble-icon')
    expect(svg.attributes('width')).toBe('14')
    expect(svg.attributes('height')).toBe('14')
    expect(svg.findAll('circle')).toHaveLength(3)
    expect(svg.get('path').attributes('fill')).toBe('#61c6ff')
    // [run mark][chat mark] name
    const kids = [...first.element.children].map(el => el.getAttribute('data-test'))
    expect(kids.slice(0, 2)).toEqual(['designer-flow-tab-run', 'designer-flow-tab-chat'])
    expect(first.text()).toBe('설정불일치')
    expect(second.find('[data-test="designer-flow-tab-chat"]').exists()).toBe(false)
  })

  it('is the same component the right dock draws for its conversation tab', () => {
    const dock = readFileSync(resolve(__dirname, '../../../src/features/designer/shell/DesignerRightDock.vue'), 'utf8')
    expect(dock).toMatch(/<ChatBubbleIcon v-if="tab\.key === 'conversation'"[^>]*:size="14"/)
    expect(dock).not.toContain("tab.key === 'conversation'\">")
    i18n.global.locale.value = 'ko'
    wrapper = mount(FlowTabStrip, {
      props: { tabs: [{ flowId: 'a', name: 'A' }], activeFlowId: 'a', chatStates: { a: true } },
      global: { plugins: [i18n] },
    })
    expect(bubblePath(wrapper.element.querySelector('[data-test="designer-flow-tab-chat"]')))
      .toBe('M3 2.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H7l-4 3v-3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z')
  })
})
