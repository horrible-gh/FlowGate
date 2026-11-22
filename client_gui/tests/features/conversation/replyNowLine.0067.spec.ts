import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import TimelineActivity from '@/features/conversation/TimelineActivity.vue'
import type { StatusActivity } from '@/features/conversation/types'
import type { TimelineItem } from '@/features/conversation/timeline'

// flowgate.v02test.0067 card 3: [지금 멈추고 다시 답하기] sits on the follow-up line ("앞 답변이 끝나면 이어서 답합니다"),
// only while a message waits; in edit mode it warns that a stop can leave an edit half done.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const line = (code = 'follow_up_waiting'): StatusActivity => ({
  kind: 'status', id: 'local-status-follow-up', chatId: 'c1', createdAt: null, tone: 'info', code, text: '1',
})
const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

describe('0067 card 3: the follow-up line\'s button', () => {
  it('shows the waiting line with [지금 멈추고 다시 답하기] and emits reply-now when pressed', async () => {
    wrapper = mount(TimelineActivity, { props: { activity: line(), replyNow: { stopping: false, editsFiles: false } }, global: { plugins: [i18n] } })
    expect(wrapper.text()).toContain('앞 답변이 끝나면 이어서 답합니다')
    const button = find('tl-reply-now')
    expect(button.exists()).toBe(true)
    expect(button.text()).toBe('지금 멈추고 다시 답하기')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    expect(wrapper.emitted('reply-now')).toHaveLength(1)
  })

  it('has no button without the replyNow prop (no waiting message) and none on another status line', () => {
    wrapper = mount(TimelineActivity, { props: { activity: line() }, global: { plugins: [i18n] } })
    expect(find('tl-reply-now').exists()).toBe(false)
    wrapper.unmount()
    wrapper = mount(TimelineActivity, {
      props: { activity: line('run_cancelled'), replyNow: { stopping: false, editsFiles: false } }, global: { plugins: [i18n] },
    })
    expect(find('tl-reply-now').exists()).toBe(false)
  })

  it('while the stop is under way the button is disabled and says so (no second press)', async () => {
    wrapper = mount(TimelineActivity, { props: { activity: line(), replyNow: { stopping: true, editsFiles: false } }, global: { plugins: [i18n] } })
    const button = find('tl-reply-now')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('멈추는 중…')
    await button.trigger('click')
    expect(wrapper.emitted('reply-now')).toBeUndefined()
  })

  it('edit mode: the button carries 「하던 수정이 중간까지 남을 수 있음」 as its hover text; read mode does not', () => {
    wrapper = mount(TimelineActivity, { props: { activity: line(), replyNow: { stopping: false, editsFiles: true } }, global: { plugins: [i18n] } })
    expect(find('tl-reply-now').attributes('title')).toBe('하던 수정이 중간까지 남을 수 있음')
    expect(find('tl-reply-now-warning').text()).toBe('하던 수정이 중간까지 남을 수 있음')
    wrapper.unmount()
    wrapper = mount(TimelineActivity, { props: { activity: line(), replyNow: { stopping: false, editsFiles: false } }, global: { plugins: [i18n] } })
    expect(find('tl-reply-now').attributes('title')).toBeUndefined()
    expect(find('tl-reply-now-warning').exists()).toBe(false)
  })

  it('read-only viewers cannot press it', () => {
    wrapper = mount(TimelineActivity, {
      props: { activity: line(), readOnly: true, replyNow: { stopping: false, editsFiles: false } }, global: { plugins: [i18n] },
    })
    expect(find('tl-reply-now').attributes('disabled')).toBeDefined()
  })

  it('the timeline hands the state to the follow-up line only, and passes the press up', async () => {
    const items: TimelineItem[] = [
      { kind: 'status', key: 's1', activity: line('run_cancelled') },
      { kind: 'status', key: 's2', activity: line() },
    ]
    wrapper = mount(ConversationTimeline, {
      props: {
        items, status: 'ready', hasMoreBefore: false, loadingOlder: false, deciding: [], emptyText: '', chatId: 'c1',
        replyNow: { stopping: false, editsFiles: false },
      },
      global: { plugins: [i18n] },
    })
    const buttons = wrapper.findAll('[data-test="tl-reply-now"]')
    expect(buttons).toHaveLength(1)
    await buttons[0].trigger('click')
    expect(wrapper.emitted('reply-now')).toHaveLength(1)
  })
})
