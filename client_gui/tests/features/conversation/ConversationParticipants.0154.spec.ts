import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ConversationParticipants from '@/features/conversation/ConversationParticipants.vue'
import type { ConversationParticipant } from '@/features/conversation/types'

registerAppMessages(i18n)
let wrapper: VueWrapper | null = null
let width = 240
let chipWidth = 60
let resize: () => void
const disconnect = vi.fn()
const people = (count: number): ConversationParticipant[] => Array.from({ length: count }, (_, i) => ({
  key: `ai:fixture-${i}`, kind: i === 0 ? 'user' : 'ai', displayName: `Model ${i}`, lastReadSeq: i + 10,
}))
const visible = () => wrapper!.findAll('[data-test="conv-participant"]').filter((chip) => chip.isVisible())
const more = () => wrapper!.find('[data-test="conv-participants-more"]')
const entries = () => wrapper!.findAll('[data-test="conv-participant-detail"]')

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  width = 240
  chipWidth = 60
  disconnect.mockClear()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resize = callback }
    observe() {}
    disconnect = disconnect
  })
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('conv-people-track') ? width : 0
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const measured = this.classList.contains('conv-people-more') ? ((this.textContent?.trim().length ?? 0) > 2 ? 40 : 32) : chipWidth
    return { x: 0, y: 0, left: 0, top: 0, right: measured, bottom: 21, width: measured, height: 21, toJSON() {} }
  })
  const originalStyle = window.getComputedStyle.bind(window)
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => {
    const style = originalStyle(element)
    if (element.classList.contains('conv-people-track')) Object.defineProperty(style, 'columnGap', { value: '5px' })
    return style
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function render(count: number) {
  wrapper = mount(ConversationParticipants, { props: { participants: people(count) }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
}

describe('participant overflow (0154)', () => {
  it.each([
    [1, 90, 1], [3, 90, 0], [9, 90, 0], [15, 90, 0],
    [1, 240, 1], [3, 240, 3], [9, 240, 3], [15, 240, 3],
    [1, 800, 1], [3, 800, 3], [9, 800, 9], [15, 800, 11],
  ])('%i participants in %i px show %i complete chips', async (count, available, shown) => {
    width = available
    await render(count)
    expect(visible()).toHaveLength(shown)
    if (shown === count) expect(more().exists()).toBe(false)
    else {
      expect(more().text()).toBe(`+${count - shown}`)
      expect(more().attributes('aria-label')).toBe(`전체 참여자 ${count}명 보기`)
      await more().trigger('click')
      expect(entries()).toHaveLength(count)
      expect(entries().map((item) => item.attributes('data-participant-key'))).toEqual(people(count).map((p) => p.key))
    }
  })

  it('reflows on width and intrinsic chip size changes, including hidden then restored panels', async () => {
    await render(9)
    expect(visible()).toHaveLength(3)
    width = 800
    resize()
    await flushPromises()
    expect(visible()).toHaveLength(9)
    chipWidth = 300
    resize()
    await flushPromises()
    expect(visible()).toHaveLength(2)
    width = 0
    resize()
    width = 90
    resize()
    await flushPromises()
    expect(visible()).toHaveLength(0)
    expect(more().text()).toBe('+9')
  })

  it('keeps an open list current for name, kind and count changes', async () => {
    await render(9)
    await more().trigger('click')
    const updated = people(15)
    updated[0] = { key: 'actual:user-key', displayName: '실제 표시명', kind: 'reviewer', lastReadSeq: 12345 }
    await wrapper!.setProps({ participants: updated })
    expect(entries()).toHaveLength(15)
    expect(entries()[0].text()).toContain('실제 표시명')
    expect(entries()[0].text()).toContain('reviewer')
    expect(entries()[0].text()).not.toContain('actual:user-key')
    expect(entries()[0].text()).not.toContain('12345')
    expect(entries()[0].attributes('title')).toBe('실제 표시명 · reviewer · actual:user-key')
    expect(entries()[0].attributes('data-last-read-seq')).toBe('12345')
    expect(more().text()).toBe('+12')
    await wrapper!.setProps({ participants: people(1) })
    expect(entries()).toHaveLength(1)
    expect(more().exists()).toBe(false)
    await wrapper!.setProps({ participants: [] })
    expect(wrapper!.find('[data-test="conv-participants"]').exists()).toBe(false)
    await wrapper!.setProps({ participants: people(9) })
    expect(more().text()).toBe('+6')
    expect(entries()).toHaveLength(0)
  })

  it('lists only name and kind per entry, with no read position (#N), keeping other details in the tooltip', async () => {
    await render(15)
    await more().trigger('click')
    const list = wrapper!.get('[data-test="conv-participants-list"]')
    expect(list.text()).not.toMatch(/#\d|읽음|ai:fixture/)
    entries().forEach((item, i) => {
      const p = people(15)[i]
      expect(item.find('.conv-people-name').text()).toBe(p.displayName)
      expect(item.find('.conv-people-kind').text()).toBe(i === 0 ? '사용자' : 'AI')
      expect(item.text().split(p.displayName)).toHaveLength(2)
      expect(item.attributes('title')).toBe(`${p.displayName} · ${i === 0 ? '사용자' : 'AI'} · ${p.key}`)
      expect(item.attributes('data-last-read-seq')).toBe(String(p.lastReadSeq))
    })
  })

  it('shows chips by name only, without the read position, and focuses the detail of a clicked chip', async () => {
    await render(3)
    const chip = visible()[1]
    expect(chip.attributes('title')).toBe('Model 1 · AI · ai:fixture-1')
    expect(chip.attributes('aria-label')).toBe('Model 1 · AI · ai:fixture-1')
    expect(chip.attributes('data-last-read-seq')).toBe('11')
    expect(visible().map((c) => c.text().replace(/\s+/g, ''))).toEqual(['●Model0', '◆Model1', '◆Model2'])
    expect(wrapper!.find('[data-test="conv-participants"]').text()).not.toMatch(/#\d/)
    await chip.trigger('click')
    expect(document.activeElement).toBe(entries()[1].element)
    await entries()[1].trigger('keydown', { key: 'Escape' })
    expect(entries()).toHaveLength(0)
    expect(document.activeElement).toBe(chip.element)
  })

  it('supports keyboard navigation, Escape, outside click and focus leaving the strip', async () => {
    await render(15)
    const trigger = more()
    ;(trigger.element as HTMLElement).focus()
    await trigger.trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(entries()[0].element)
    await entries()[0].trigger('keydown', { key: 'End' })
    expect(document.activeElement).toBe(entries()[14].element)
    await entries()[14].trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(entries()[0].element)
    await entries()[0].trigger('keydown', { key: 'Escape' })
    expect(document.activeElement).toBe(trigger.element)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    await trigger.trigger('click')
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(entries()).toHaveLength(0)
    await trigger.trigger('click')
    const outside = document.createElement('button')
    document.body.append(outside)
    outside.focus()
    await flushPromises()
    expect(entries()).toHaveLength(0)
  })

  it('returns focus to overflow when the focused chip folds and cleans up observers', async () => {
    await render(3)
    ;(visible()[2].element as HTMLElement).focus()
    width = 90
    resize()
    await flushPromises()
    expect(document.activeElement).toBe(more().element)
    width = 800
    resize()
    await flushPromises()
    expect(document.activeElement).toBe(visible()[0].element)
    wrapper!.unmount()
    wrapper = null
    expect(disconnect).toHaveBeenCalled()
  })
})
