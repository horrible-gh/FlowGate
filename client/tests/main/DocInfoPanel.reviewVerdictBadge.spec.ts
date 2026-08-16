// group 0422 R0001: "사이드바 검수의견 헤더에 최소한 몇건 지적있는지 통과했는지 배지정도는
// 좀 달아줘라". TR0003 rev0 put one aggregate pill on the merged section headline, and rev1
// put badges outside each card. The rev2 rejection names the exact target: every verdict
// badge belongs inside its own .dip-ai-comment-toggle header.
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { AiReview } from '@main/types/aiReview'

function mountPanel(reviews: AiReview[]) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0014.0002-D',
      typeCode: 'D',
      reviewStatus: 'pending_review',
      rejectReason: null,
      rejectionHistory: [],
      aiReview: reviews[reviews.length - 1] ?? null,
      aiReviewHistory: reviews,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

function mergedSection(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.dip-section')
    .find((s) => s.find('.dip-sec-toggle').exists() && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject')))!
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel AI검수·반려 comment-toggle verdict badge (0422 R0001 / TR0003 rev2)', () => {
  it('shows no verdict badge when there is no AI review at all', () => {
    const wrapper = mountPanel([])
    const section = mergedSection(wrapper)
    expect(section.findAll('.dip-ai-verdict').length).toBe(0)
  })

  it('draws one badge per review with the right label and tone, in feed order', () => {
    const wrapper = mountPanel([
      { id: 1, verdict: 'pass', finding_count: 0, comment: '문제 없음', reviewed_at: '2026-06-12T20:00:00+09:00' },
      { id: 2, verdict: 'issues', finding_count: 2, comment: '두 가지 지적', reviewed_at: '2026-06-12T20:10:00+09:00' },
    ])
    const section = mergedSection(wrapper)
    const toggles = section.findAll('.dip-ai-comment-toggle')
    const badges = section.findAll('.dip-ai-comment-toggle .dip-ai-verdict')
    expect(toggles.length).toBe(2)
    expect(badges.length).toBe(2)
    expect(toggles.every((toggle) => toggle.findAll('.dip-ai-verdict').length === 1)).toBe(true)
    // newest review (id 2) sorts first in the feed.
    expect(badges[0].classes()).toContain('warn')
    expect(badges[0].text()).toBe(i18n.global.t('main.doc_info_panel.ai_verdict_issues', { n: 2 }))
    expect(badges[1].classes()).toContain('pass')
    expect(badges[1].text()).toBe(i18n.global.t('main.doc_info_panel.ai_verdict_pass'))
  })

  it('shows a "보류" badge for a hold verdict', () => {
    const wrapper = mountPanel([
      { id: 1, verdict: 'hold', finding_count: 0, comment: '재검토 필요', reviewed_at: '2026-06-12T20:00:00+09:00' },
    ])
    const section = mergedSection(wrapper)
    const badge = section.find('.dip-ai-comment-toggle .dip-ai-verdict')
    expect(badge.classes()).toContain('warn')
    expect(badge.text()).toBe(i18n.global.t('main.doc_info_panel.ai_verdict_hold'))
  })

  it('does not create a badge row when a review has no 검수의견 toggle', () => {
    const wrapper = mountPanel([
      { id: 1, verdict: 'issues', finding_count: 3, comment: null, reviewed_at: '2026-06-12T20:00:00+09:00' },
    ])
    const section = mergedSection(wrapper)
    expect(section.find('.dip-ai-entry').exists()).toBe(false)
    expect(section.find('.dip-ai-comment-toggle').exists()).toBe(false)
    expect(section.find('.dip-ai-verdict').exists()).toBe(false)
  })
})
