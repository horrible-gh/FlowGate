import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { AiReview } from '@main/types/aiReview'

// R0043 "AI검수 표시 개선": the AI review comment was always shown in full, so a long
// comment stretched the right panel and pushed the rejection section below it out of
// view. The comment now folds like the rejection reason / AI response — clamped by
// default, expanding to a height-capped, scrollable body (14px amber scrollbar), and
// the findings list is height-capped too.

function mountPanel(aiReview: AiReview) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0043.0002-D',
      typeCode: 'D',
      reviewStatus: 'pending_review',
      rejectReason: null,
      aiReview,
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

describe('DocInfoPanel AI review comment (R0043)', () => {
  it('puts the comment in its own foldable, height-capped body', () => {
    const wrapper = mountPanel({
      verdict: 'issues',
      finding_count: 0,
      comment: '검수 코멘트 '.repeat(400),
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    const box = wrapper.find('.dip-ai-comment')
    const body = wrapper.find('.dip-ai-comment-body')
    expect(box.exists()).toBe(true)
    expect(body.exists()).toBe(true)
    expect(body.text()).toContain('검수 코멘트')
  })

  it('folds the comment by default and toggles open on the toggle click', async () => {
    const wrapper = mountPanel({
      verdict: 'issues',
      finding_count: 0,
      comment: '짧은 코멘트',
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    const box = wrapper.find('.dip-ai-comment')
    const toggle = wrapper.find('.dip-ai-comment-toggle')
    expect(box.classes()).not.toContain('open')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(wrapper.find('.dip-ai-comment').classes()).toContain('open')
    expect(wrapper.find('.dip-ai-comment-toggle').attributes('aria-expanded')).toBe('true')
  })

  it('renders no comment box when the review has no comment', () => {
    const wrapper = mountPanel({
      verdict: 'pass',
      finding_count: 0,
      comment: null,
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    expect(wrapper.find('.dip-ai-comment').exists()).toBe(false)
  })

  it('renders findings under the verdict toggle (the comment fold is independent)', async () => {
    const wrapper = mountPanel({
      verdict: 'issues',
      finding_count: 2,
      findings: [
        { locus: 'a.ts:1', note: '지적1' },
        { locus: 'b.ts:2', note: '지적2' },
      ],
      comment: '코멘트',
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    // findings collapsed by default
    expect(wrapper.find('.dip-ai-findings').exists()).toBe(false)
    await wrapper.find('.dip-ai-verdict--toggle').trigger('click')
    expect(wrapper.find('.dip-ai-findings').exists()).toBe(true)
    // the comment fold is its own toggle, untouched by expanding findings
    expect(wrapper.find('.dip-ai-comment').classes()).not.toContain('open')
  })
})
