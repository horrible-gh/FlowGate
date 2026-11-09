import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import type { TimelineItem } from '@/features/conversation/timeline'
import type { ConversationTurn } from '@/features/conversation/types'

// flowgate.v02.0157 — Markdown tables, a copy per code block and the whole-message copy in the Designer chat.
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async (_text: string, _host?: HTMLElement | null) => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const turn = (seq: number, speaker: 'user' | 'ai', body: string): ConversationTurn => ({
  seq, speaker, body, createdAt: '2026-11-09T09:00:00Z', displayName: speaker === 'ai' ? 'Claude Opus 5.5' : null,
})

const TABLE = ['| 이름 | 값 |', '| --- | --- |', '| a | 1 |', '| b | 2 |'].join('\n')
const TWO_CODES = ['첫째:', '', '```ts', 'const a = 1 < 2', '```', '', '둘째:', '', '```sh', 'echo "second"', '```'].join('\n')

let wrappers: VueWrapper[] = []
function mountMessage(t: ConversationTurn, props: Record<string, unknown> = {}) {
  const wrapper = mount(TimelineMessage, {
    props: { turn: t, pending: false, failed: false, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  clipboard.copyToClipboard.mockReset()
  clipboard.copyToClipboard.mockResolvedValue(true)
})

afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount()
  wrappers = []
  vi.useRealTimers()
})

describe('TimelineMessage Markdown (flowgate.v02.0157)', () => {
  it('renders an AI message table as a table and keeps a user message as plain text', () => {
    const ai = mountMessage(turn(2, 'ai', `표입니다.\n\n${TABLE}`))
    const table = ai.get('[data-test="tl-md"] table')
    expect(table.findAll('th').map((th) => th.text())).toEqual(['이름', '값'])
    expect(table.findAll('tbody tr')).toHaveLength(2)

    const user = mountMessage(turn(1, 'user', TABLE))
    expect(user.find('table').exists()).toBe(false)
    expect(user.find('[data-test="tl-md"]').exists()).toBe(false)
    expect(user.get('.tl-text').text()).toBe(TABLE)
  })

  it('shows raw HTML in an AI message as text (no element, no image, no unsafe link)', () => {
    const wrapper = mountMessage(turn(2, 'ai', '<img src=x onerror="alert(1)"> <script>alert(2)</script>\n\n[x](javascript:alert(3)) ![i](https://e.x/i.png)'))
    const md = wrapper.get('[data-test="tl-md"]')
    expect(md.find('img').exists()).toBe(false)
    expect(md.find('script').exists()).toBe(false)
    expect(md.find('a').exists()).toBe(false)
    expect(md.text()).toContain('<script>alert(2)</script>')
  })

  it('copies only the code of the pressed block and resets its button afterwards', async () => {
    vi.useFakeTimers()
    const wrapper = mountMessage(turn(2, 'ai', TWO_CODES))
    const buttons = wrapper.findAll('[data-test="tl-md"] .code-copy-btn')
    expect(buttons).toHaveLength(2)
    await buttons[1].trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard).toHaveBeenCalledTimes(1)
    expect(clipboard.copyToClipboard.mock.calls[0][0]).toBe('echo "second"')
    expect(buttons[1].text()).toBe('✓')
    expect(buttons[1].classes()).toContain('code-copy-btn--copied')
    expect(buttons[0].text()).toBe('⧉')

    await buttons[0].trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard.mock.calls[1][0]).toBe('const a = 1 < 2')

    vi.advanceTimersByTime(1500)
    expect(buttons.map((b) => b.text())).toEqual(['⧉', '⧉'])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('copies the whole message as written (Markdown source), on both sides', async () => {
    const aiBody = `${TABLE}\n\n${TWO_CODES}`
    const ai = mountMessage(turn(2, 'ai', aiBody))
    await ai.get('[data-test="tl-copy"]').trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard.mock.calls[0][0]).toBe(aiBody)
    expect(ai.get('[data-test="tl-copy"]').attributes('data-state')).toBe('done')
    expect(ai.get('[data-test="tl-copy"]').attributes('aria-label')).toBe(String(i18n.global.t('gui.conversation.message.copied')))
    expect(ai.get('[role="status"]').text()).toBe(String(i18n.global.t('gui.conversation.message.copied')))

    const user = mountMessage(turn(1, 'user', '  줄 하나\n줄 둘  '))
    clipboard.copyToClipboard.mockResolvedValueOnce(false)
    await user.get('[data-test="tl-copy"]').trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard.mock.calls[1][0]).toBe('  줄 하나\n줄 둘  ')
    expect(user.get('[data-test="tl-copy"]').attributes('data-state')).toBe('failed')
    expect(user.get('[data-test="tl-copy"]').text()).toBe('✕')
  })

  it('keeps the copy button inside the meta line next to the time', () => {
    const wrapper = mountMessage(turn(2, 'ai', 'hi'))
    const meta = wrapper.get('.tl-meta')
    expect(meta.find('[data-test="tl-copy"]').exists()).toBe(true)
    expect(meta.findAll('*').map((n) => n.classes()).flat()).toEqual(expect.arrayContaining(['tl-time', 'tl-copy']))
    expect(mountMessage(turn(3, 'ai', '')).find('[data-test="tl-copy"]').exists()).toBe(false)
  })

  it('drops a copy result that arrives after the body changed', async () => {
    vi.useFakeTimers()
    const gate = deferred<boolean>()
    clipboard.copyToClipboard.mockReturnValueOnce(gate.promise)
    const wrapper = mountMessage(turn(2, 'ai', '```\nold\n```'))
    await wrapper.get('[data-test="tl-copy"]').trigger('click')
    await wrapper.get('.code-copy-btn').trigger('click')
    await wrapper.setProps({ turn: turn(2, 'ai', '```\nnew body\n```') })
    gate.resolve(true)
    await flushPromises()
    expect(wrapper.get('[data-test="tl-copy"]').attributes('data-state')).toBe('idle')
    expect(wrapper.get('.code-copy-btn').text()).toBe('⧉')
    expect(wrapper.get('[data-test="tl-md"] code').text()).toBe('new body')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('leaves no timer and no late update when unmounted during or after a copy', async () => {
    vi.useFakeTimers()
    const gate = deferred<boolean>()
    clipboard.copyToClipboard.mockReturnValueOnce(gate.promise)
    const inFlight = mountMessage(turn(2, 'ai', 'a'))
    await inFlight.get('[data-test="tl-copy"]').trigger('click')
    inFlight.unmount()
    wrappers = wrappers.filter((w) => w !== inFlight)
    gate.resolve(true)
    await flushPromises()
    expect(vi.getTimerCount()).toBe(0)

    const flashing = mountMessage(turn(3, 'ai', '```\nx\n```'))
    await flashing.get('[data-test="tl-copy"]').trigger('click')
    await flashing.get('.code-copy-btn').trigger('click')
    await flushPromises()
    expect(vi.getTimerCount()).toBe(2)
    flashing.unmount()
    wrappers = wrappers.filter((w) => w !== flashing)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('reports only the latest of quick repeated clicks, with one reset timer', async () => {
    vi.useFakeTimers()
    const first = deferred<boolean>()
    const second = deferred<boolean>()
    clipboard.copyToClipboard.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const wrapper = mountMessage(turn(2, 'ai', 'body'))
    const button = wrapper.get('[data-test="tl-copy"]')
    await button.trigger('click')
    await button.trigger('click')
    second.resolve(true)
    await flushPromises()
    first.resolve(false)
    await flushPromises()
    expect(button.attributes('data-state')).toBe('done')
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(1500)
    await nextTick()
    expect(button.attributes('data-state')).toBe('idle')
  })

  it('follows the language for the code copy label and the whole copy label', async () => {
    const wrapper = mountMessage(turn(2, 'ai', '```\nx\n```'))
    expect(wrapper.get('.code-copy-btn').attributes('aria-label')).toBe('코드 복사')
    expect(wrapper.get('[data-test="tl-copy"]').attributes('aria-label')).toBe('원문 복사')
    i18n.global.locale.value = 'en'
    await nextTick()
    expect(wrapper.get('.code-copy-btn').attributes('aria-label')).toBe('Copy code')
    expect(wrapper.get('[data-test="tl-copy"]').attributes('aria-label')).toBe('Copy original text')
    i18n.global.locale.value = 'ja'
    await nextTick()
    expect(wrapper.get('.code-copy-btn').attributes('aria-label')).toBe('コードをコピー')
  })

  it('copies exactly once per click after a remount (re-entry)', async () => {
    const first = mountMessage(turn(2, 'ai', '```\nx\n```'))
    first.unmount()
    wrappers = wrappers.filter((w) => w !== first)
    const again = mountMessage(turn(2, 'ai', '```\nx\n```'))
    await again.get('[data-test="tl-copy"]').trigger('click')
    await again.get('.code-copy-btn').trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard).toHaveBeenCalledTimes(2)
    expect(clipboard.copyToClipboard.mock.calls.map((c) => c[0])).toEqual(['```\nx\n```', 'x'])
  })
})

describe('ConversationTimeline tail follow on an in-place body update (flowgate.v02.0157)', () => {
  function messageItem(seq: number, body: string): TimelineItem {
    return { kind: 'message', key: `turn:${seq}`, turn: turn(seq, 'ai', body), pending: false, failed: false, localId: null }
  }

  async function mountTimeline() {
    const wrapper = mount(ConversationTimeline, {
      props: { items: [messageItem(1, 'q'), messageItem(2, '| a |')], status: 'ready', hasMoreBefore: false, loadingOlder: false,
        deciding: [], emptyText: '', chatId: 'c' },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    wrappers.push(wrapper)
    const el = wrapper.get('[data-test="conv-log"]').element as HTMLElement
    let top = 0
    let height = 1000
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 200 })
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => height })
    Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => { top = v } })
    await flushPromises()
    return { wrapper, el, grow: (h: number) => { height = h }, at: () => top }
  }

  it('keeps a reader at the bottom with the newest message as its body grows', async () => {
    const { wrapper, el, grow, at } = await mountTimeline()
    el.scrollTop = 800
    await wrapper.get('[data-test="conv-log"]').trigger('scroll')
    grow(1600)
    await wrapper.setProps({ items: [messageItem(1, 'q'), messageItem(2, '| a |\n| --- |\n| 1 |')] })
    await flushPromises()
    expect(at()).toBe(1600)
  })

  it('never moves a reader who scrolled up into history', async () => {
    const { wrapper, el, grow, at } = await mountTimeline()
    el.scrollTop = 100
    await wrapper.get('[data-test="conv-log"]').trigger('scroll')
    grow(1600)
    await wrapper.setProps({ items: [messageItem(1, 'q'), messageItem(2, '| a |\n| --- |\n| 1 |')] })
    await flushPromises()
    expect(at()).toBe(100)
  })
})
