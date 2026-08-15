import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { AiReview } from '@main/types/aiReview'

// R0043 "AI검수 표시 개선": the AI review comment was always shown in full, so a long
// comment stretched the right panel and pushed the rejection section below it out of
// view. The comment folds — clamped by default, opening to a still-clamped body.
//
// 0311 TR0005 rev5 반려: (§1) the panel no longer draws the entry head (시각 · AI), the
// verdict badge or the findings list — the comment fold is ALL that is left of a review
// in the panel, and it is labelled 「검수 의견」 now (§2). (§4) opening it no longer turns
// it into a scroll box: it goes from 2 clamped lines to 6, so a long comment always ends
// in an ellipsis and the full text is read in [전체보기].

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

  it('draws no review card at all when the review has no comment (rev5 §1)', () => {
    // With the head, the badge and the findings gone there is nothing left to draw, so
    // the review takes no row in the panel. It is still listed in [전체보기].
    const wrapper = mountPanel({
      verdict: 'pass',
      finding_count: 0,
      comment: null,
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    expect(wrapper.find('.dip-ai-comment').exists()).toBe(false)
    expect(wrapper.find('.dip-ai-entry').exists()).toBe(false)
  })

  it('draws no entry head, verdict badge or findings list in the panel (rev5 §1)', () => {
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
    // the card exists, and the comment fold is the whole of it
    expect(wrapper.find('.dip-ai-entry').exists()).toBe(true)
    expect(wrapper.find('.dip-ai-comment').exists()).toBe(true)
    // 반려 §1: none of this appears any more
    expect(wrapper.find('.dip-ai-entry-head').exists()).toBe(false)
    expect(wrapper.find('.dip-ai-meta').exists()).toBe(false)
    expect(wrapper.find('.dip-ai-verdict').exists()).toBe(false)
    expect(wrapper.find('.dip-ai-verdict--toggle').exists()).toBe(false)
    expect(wrapper.find('.dip-ai-findings').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('지적1')
  })

  it('labels the fold 「검수 의견」 (rev5 §2)', () => {
    const wrapper = mountPanel({
      verdict: 'issues',
      finding_count: 0,
      comment: '코멘트',
      reviewed_at: '2026-06-13T13:00:00+09:00',
    })
    // the suite runs in the default locale, so pin the Korean wording on the ko bundle
    const ko = (i18n.global.getLocaleMessage('ko') as any).main.doc_info_panel
    expect(ko.ai_comment_label).toBe('검수 의견')
    expect(ko.ai_comment_label).not.toBe('검수 코멘트')
    expect(wrapper.find('.dip-ai-comment-label').text())
      .toContain(i18n.global.t('main.doc_info_panel.ai_comment_label'))
  })
})
