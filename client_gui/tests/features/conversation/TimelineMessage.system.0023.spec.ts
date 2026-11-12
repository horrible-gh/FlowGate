import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import { runBodyState } from '@/features/conversation/runAssessment'
import type { ConversationTurn } from '@/features/conversation/types'

// flowgate.v02test.0023 — the chat commit result arrives as a FlowGate turn (speaker `system`) of the conversation:
// shown as Markdown under a FlowGate label, never as the AI's reply.
registerAppMessages(i18n)

const COMMIT = '채팅 작업을 커밋했습니다: `abc1234` fix(chat): x (파일 1개)\n\n- `a.py`'
const system = (body = COMMIT): ConversationTurn => ({
  seq: 5, speaker: 'system', body, createdAt: '2026-11-12T07:00:00Z', displayName: 'FlowGate', sourceRunId: 'aiv2_1',
})

let wrappers: VueWrapper[] = []
function mountMessage(t: ConversationTurn) {
  const wrapper = mount(TimelineMessage, {
    props: { turn: t, pending: false, failed: false },
    global: { plugins: [i18n] },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount()
  wrappers = []
})

describe('FlowGate turn (flowgate.v02test.0023)', () => {
  it('is labelled FlowGate, rendered as Markdown and carries no model badge', () => {
    const wrapper = mountMessage(system())
    const root = wrapper.get('[data-test="tl-message"]')
    expect(root.attributes('data-speaker')).toBe('system')
    expect(root.classes()).toContain('tl-msg--system')
    expect(wrapper.get('.tl-speaker').text()).toContain('FlowGate')
    expect(wrapper.find('[data-test="tl-provider"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="tl-md"]').findAll('code').map((c) => c.text())).toEqual(['abc1234', 'a.py'])
  })

  it('an AI message keeps its AI label and model badge', () => {
    const wrapper = mountMessage({ ...system('done'), speaker: 'ai', displayName: 'Claude Opus 5.5' })
    expect(wrapper.get('[data-test="tl-message"]').attributes('data-speaker')).toBe('ai')
    expect(wrapper.get('[data-test="tl-provider"]').text()).toBe('Claude Opus 5.5')
  })

  it('never counts as the run reply', () => {
    expect(runBodyState([system()], 'aiv2_1', 'complete')).toBe('absent')
  })
})
