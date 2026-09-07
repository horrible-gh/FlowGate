import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import QaReviewHistoryDialog from '@main/components/QaReviewHistoryDialog.vue'

// TR0005 rev6 반려 — "질의·반려 전체보기" 가 아니라 "검수·반려 전체보기", "AI 검수"는
// 왜 접는거 없냐, "질의"는 빼라. 세 줄 모두 이 다이얼로그(구 QaReviewHistoryDialog) 얘기다.

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(QaReviewHistoryDialog, {
    props: { visible: true, reviews: [], rejections: [], ...props },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

const REVIEW = {
  id: 21,
  reviewer_name: '검수봇',
  verdict: 'issues' as const,
  finding_count: 1,
  findings: [{ locus: 'app.py:12', note: '널 검사가 없다' }],
  comment: '전반적으로는 통과 가능하나 몇 가지 고칠 점이 있습니다.',
  reviewed_at: '2026-06-12T20:50:00+09:00',
  review_provider: {
    run_id: 'air_20260906_000001',
    requested_provider_id: 'aip_sonnet',
    actual_provider_id: 'aip_opus',
    actual_provider_name: 'Opus',
    provider_source: 'reviewer_override',
    attempt_no: 2,
    fallback_used: true,
  },
}

describe('QaReviewHistoryDialog title (TR0005 rev6 반려 §1)', () => {
  it('titles itself 검수·반려 전체보기, not 질의·반려', () => {
    // pin the ko bundle — the suite may run in any active locale (main.ts default is 'ko',
    // but other suites in this run switch the shared i18n singleton to 'en').
    const ko = (i18n.global.getLocaleMessage('ko') as any).main.qa_review_history
    expect(ko.title).toBe('검수·반려 전체보기')
    expect(ko.title).not.toContain('질의')
    expect(ko.desc).not.toContain('질의')
  })
})

describe('QaReviewHistoryDialog AI 검수 fold (TR0005 rev6 반려 §2)', () => {
  it('gives an ai_review entry the same fold control the reject entry has, open by default', async () => {
    const wrapper = mountDialog({ reviews: [REVIEW] })
    const fold = wrapper.find('.rhd-item--review .rhd-fold')
    expect(fold.exists()).toBe(true)
    expect(fold.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.rhd-comment').classes()).not.toContain('collapsed')

    await fold.trigger('click')
    expect(wrapper.find('.rhd-comment').classes()).toContain('collapsed')
  })

  it('omits the fold button when the review has no comment (nothing to fold)', () => {
    const wrapper = mountDialog({ reviews: [{ ...REVIEW, comment: null }] })
    expect(wrapper.find('.rhd-item--review .rhd-fold').exists()).toBe(false)
  })
})

describe('QaReviewHistoryDialog provider provenance', () => {
  it('shows actual provider and highlights requested/actual mismatch', () => {
    const wrapper = mountDialog({ reviews: [REVIEW] })
    const provider = wrapper.find('.rhd-provider')
    expect(provider.text()).toContain('Opus')
    expect(provider.text()).toContain('aip_sonnet')
    expect(provider.classes()).toContain('rhd-provider--mismatch')
  })

  it('shows only actual provider when requested and actual match', () => {
    const matching = {
      ...REVIEW,
      review_provider: {
        ...REVIEW.review_provider,
        requested_provider_id: 'aip_opus',
        fallback_used: false,
      },
    }
    const wrapper = mountDialog({ reviews: [matching] })
    const provider = wrapper.find('.rhd-provider')
    expect(provider.text()).toContain('Opus')
    expect(provider.classes()).not.toContain('rhd-provider--mismatch')
    expect(provider.text()).not.toContain('aip_opus')
  })

  it('keeps legacy reviews without provenance renderable', () => {
    const legacy = { ...REVIEW, review_provider: undefined }
    const wrapper = mountDialog({ reviews: [legacy] })
    expect(wrapper.find('.rhd-item--review').exists()).toBe(true)
    expect(wrapper.find('.rhd-provider').exists()).toBe(false)
  })
})

describe('QaReviewHistoryDialog drops 질의 (TR0005 rev6 반려 §3)', () => {
  it('has no 질의 filter tab, only 전체/반려/AI 검수', () => {
    const wrapper = mountDialog()
    const tabs = wrapper.findAll('.tab-nav-item').map((t) => t.text())
    expect(tabs.some((text) => text.includes(i18n.global.t('main.qa_review_history.filter_reject')))).toBe(true)
    expect(tabs.some((text) => text.includes(i18n.global.t('main.qa_review_history.filter_ai_review')))).toBe(true)
    expect(tabs.some((text) => text.includes('질의'))).toBe(false)
  })

  it('never accepts qa props any more — the component type has none', () => {
    // qaItems/docId/submitAnswer etc. were removed from defineProps entirely; this
    // dialog only knows reviews + rejections now.
    const wrapper = mountDialog({ reviews: [REVIEW] })
    expect(wrapper.find('.qhd-entry').exists()).toBe(false)
    expect(wrapper.find('.qhd-answer-form').exists()).toBe(false)
  })
})
