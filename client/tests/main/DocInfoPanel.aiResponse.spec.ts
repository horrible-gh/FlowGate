import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// P0005/T0006: the AI response arrives with the inbox re-submission and is shown
// (read-only) under the latest rejection reason. There is no in-app input.

type RejItem = {
  rejection_id?: string
  reason: string
  rejected_at: string
  rejected_by: string | null
  ai_response?: string | null
  responded_at?: string | null
  response_revision_no?: number | null
}

function mountPanel(history: RejItem[]) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0014.0002-D',
      typeCode: 'D',
      reviewStatus: 'rejected',
      rejectReason: history[history.length - 1]?.reason ?? null,
      rejectionHistory: history,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel AI response (P0005/T0006)', () => {
  it('shows the AI response under the latest rejection reason', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '해당 부분을 수정했습니다.' },
    ])
    const resp = wrapper.find('.dip-ai-response')
    expect(resp.exists()).toBe(true)
    expect(resp.text()).toContain('해당 부분을 수정했습니다.')
  })

  it('renders nothing for the AI response when none was submitted', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: null },
    ])
    expect(wrapper.find('.dip-ai-response').exists()).toBe(false)
  })

  it('shows the AI response for the latest rejection among several', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_old', reason: '예전', rejected_at: '2026-06-12T20:00:00+09:00', rejected_by: null,
        ai_response: '예전 대응' },
      { rejection_id: 'rej_new', reason: '최신 사유', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '최신 대응' },
    ])
    const resp = wrapper.find('.dip-ai-response')
    expect(resp.exists()).toBe(true)
    expect(resp.text()).toContain('최신 대응')
    expect(resp.text()).not.toContain('예전 대응')
  })

  // rev3 (reject #3 fix): the response box was nested INSIDE the rejection quote
  // body (a box-in-a-box, "위치가 매우 이상함"). It is now a sibling threaded under
  // the quote, not a descendant of it.
  it('renders the AI response OUTSIDE the rejection quote, not nested inside it', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '수정했습니다.' },
    ])
    expect(wrapper.find('.dip-ai-response').exists()).toBe(true)
    // must NOT live inside the rejection quote box anymore
    expect(wrapper.find('.dip-reject-quote .dip-ai-response').exists()).toBe(false)
    expect(wrapper.find('.dip-reject-quote-body .dip-ai-response').exists()).toBe(false)
  })

  // rev3 (reject #3 fix): a long response must not stretch the panel
  // ("글씨가 많아지면 똑같이 길어질거아냐?"). The body is its own height-capped,
  // scrollable element, separate from the reply header.
  it('puts the response text in a height-capped scrollable body', () => {
    const long = '대응 '.repeat(400)
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: long, responded_at: '2026-06-13T07:25:00+09:00' },
    ])
    const body = wrapper.find('.dip-ai-response-body')
    expect(body.exists()).toBe(true)
    expect(body.text()).toContain('대응')
    // the reply header (label + date) is a separate element from the capped body
    expect(wrapper.find('.dip-ai-response-head .dip-ai-response-label').exists()).toBe(true)
  })

  // rev4 (reject #4 fix): the response folds like the rejection quote
  // ("대응도 접을수 있게 해"). Folded by default; the header toggles it open.
  it('folds the response by default and toggles open on header click', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '대응 내용', responded_at: '2026-06-13T07:25:00+09:00' },
    ])
    const box = wrapper.find('.dip-ai-response')
    const head = wrapper.find('.dip-ai-response-head')
    expect(box.classes()).not.toContain('open')
    expect(head.attributes('aria-expanded')).toBe('false')
    await head.trigger('click')
    expect(wrapper.find('.dip-ai-response').classes()).toContain('open')
    expect(wrapper.find('.dip-ai-response-head').attributes('aria-expanded')).toBe('true')
  })
})
