import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import source from '@/features/conversation/TimelineMessage.vue?raw'
import menuSource from '@/features/conversation/TimelineMessageMenu.vue?raw'
import type { ConversationTurn } from '@/features/conversation/types'

// flowgate.v02gui.0031 B — the AI speaker's name is no longer cut by a fixed 110px cap: it shows in full while the
// line has room and gives way (ellipsis) only when it does not. The title keeps the full name. The name also never
// widens the bubble (0007-T §3): its slot has no width of its own and only takes the room the line has left.
registerAppMessages(i18n)

const LONG_NAMES = ['Claude Sonnet 5.5(AG,A2)', 'Codex GPT 6.1 Sol(AG)']

const aiTurn = (displayName: string): ConversationTurn => ({
  seq: 3, speaker: 'ai', body: 'ok', createdAt: '2026-11-16T05:00:00Z', displayName,
})

let wrappers: VueWrapper[] = []
function mountMessage(turn: ConversationTurn, extra: Record<string, unknown> = {}) {
  const wrapper = mount(TimelineMessage, {
    props: { turn, pending: false, failed: false, ...extra },
    global: { plugins: [i18n] },
  })
  wrappers.push(wrapper)
  return wrapper
}

/** The declarations of one top-level rule (`selector { ... }`) of a component's style block. */
function ruleOf(raw: string, selector: string): string {
  const style = raw.slice(raw.indexOf('<style'))
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`(^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(style)
  expect(match, `${selector} rule`).not.toBeNull()
  return match![2]
}
const providerRule = () => ruleOf(source, '.tl-provider')

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount()
  wrappers = []
})

describe('AI speaker name badge (flowgate.v02gui.0031 B)', () => {
  it.each(LONG_NAMES)('renders the whole name %s as text, with the same value as its title', (name) => {
    const badge = mountMessage(aiTurn(name)).get('[data-test="tl-provider"]')
    expect(badge.text()).toBe(name)
    expect(badge.attributes('title')).toBe(name)
  })

  it('has no fixed width cap, and still shrinks to an ellipsis only when the line is short of room', () => {
    const rule = providerRule()
    expect(rule).not.toMatch(/(^|[\s;])max-width\s*:/)
    expect(rule).toMatch(/min-width\s*:\s*0\b/)
    expect(rule).toMatch(/overflow\s*:\s*hidden/)
    expect(rule).toMatch(/text-overflow\s*:\s*ellipsis/)
    expect(rule).toMatch(/white-space\s*:\s*nowrap/)
    expect(rule).not.toMatch(/flex(-shrink)?\s*:\s*0\s*;/)
  })

  it('keeps the speaker, time and copy button from shrinking, so the name gives way first', () => {
    expect(ruleOf(source, '.tl-speaker')).toMatch(/flex-shrink\s*:\s*0/)
    expect(ruleOf(source, '.tl-time')).toMatch(/flex-shrink\s*:\s*0/)
    expect(ruleOf(source, '.tl-copy')).toMatch(/flex-shrink\s*:\s*0/)
  })

  it('keeps the checkpoint menu from shrinking: its root (.tlm, which also carries .tl-more) is flex-shrink: 0', () => {
    // TimelineMessage.vue's own .tl-more rule only sets margins; the no-shrink declaration is on the menu's root.
    expect(ruleOf(source, '.tl-more')).not.toMatch(/flex/)
    expect(ruleOf(menuSource, '.tlm')).toMatch(/flex-shrink\s*:\s*0/)
    const wrapper = mountMessage(aiTurn(LONG_NAMES[0]), { canCheckpoint: true })
    const menu = wrapper.get('.tl-meta .tlm')
    expect(menu.classes()).toContain('tl-more')
    expect(menu.find('[data-test="tl-more"]').exists()).toBe(true)
  })

  it('puts the name badge in a slot that adds nothing to the bubble width (0007-T §3)', () => {
    const wrapper = mountMessage(aiTurn(LONG_NAMES[0]))
    const slot = wrapper.get('.tl-meta > .tl-provider-slot')
    expect(slot.find('[data-test="tl-provider"]').exists()).toBe(true)
    // The slot has no size of its own (flex-basis 0, width 0), so the bubble's width does not depend on the name; it
    // only grows into the room the line has left. Its negative margin cancels the one extra .tl-meta gap.
    const rule = ruleOf(source, '.tl-provider-slot')
    expect(rule).toMatch(/flex\s*:\s*1\s+1\s+0\s*;/)
    expect(rule).toMatch(/(^|[\s;])width\s*:\s*0\s*;/)
    expect(rule).toMatch(/min-width\s*:\s*0\s*;/)
    expect(rule).toMatch(/overflow\s*:\s*hidden/)
    expect(rule).toMatch(/margin-right\s*:\s*-5px/)
    expect(ruleOf(source, '.tl-meta')).toMatch(/gap\s*:\s*5px/)
    expect(providerRule()).toMatch(/margin-right\s*:\s*5px/)
  })

  it('still shows no badge for the user and FlowGate turns', () => {
    const user = mountMessage({ ...aiTurn('x'), speaker: 'user', displayName: null })
    const system = mountMessage({ ...aiTurn('FlowGate'), speaker: 'system' })
    expect(user.find('[data-test="tl-provider"]').exists()).toBe(false)
    expect(system.find('[data-test="tl-provider"]').exists()).toBe(false)
  })
})

