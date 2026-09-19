// 0582 T0005 §4/§8.4/§8.5/§8.6: the compact AI검수·반려 section must show the ACTUAL
// AI provider — never `issued_to`/the request/current-setting provider — for the
// review card, an automatic rejection, and a rework response, each independently.
// 0582 TR0006 rev1 §3-D: the default Q card (qaFeedVisible) needs the same convention
// for the in-app AI that asked a question, not only QaHistoryDialog's full view.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { AiReview } from '@main/types/aiReview'
import type { RejectionHistoryItem } from '@main/composables/useFlowGateToken'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const ISSUED_TO_SENTINEL = 'u-issued-to-should-not-appear'
const GOOD_EFFECTIVE_PROVIDER = 'Claude Opus 5'
const REWORK_PROVIDER = 'Codex1 GPT-5.6 Sol'

function mountPanel(opts: { reviews?: AiReview[]; rejectionHistory?: RejectionHistoryItem[]; qaItems?: any[] }) {
  const reviews = opts.reviews ?? []
  if (opts.qaItems) {
    getRequest.mockResolvedValue({ data: { qa: { items: opts.qaItems } } })
  }
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0582.0002-D',
      typeCode: 'D',
      reviewStatus: 'rejected',
      rejectReason: null,
      rejectionHistory: opts.rejectionHistory ?? [],
      aiReview: reviews[reviews.length - 1] ?? null,
      aiReviewHistory: reviews,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: { qa: { items: [] } } })
})

function mergedSection(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.dip-section')
    .find((s) => s.find('.dip-sec-toggle').exists() && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject')))!
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel AI provider badges (0582 T0005)', () => {
  it('shows "AI · {provider}" on the compact review card from review_provider', () => {
    const wrapper = mountPanel({
      reviews: [{
        id: 1, verdict: 'issues', finding_count: 1, comment: '지적 있음',
        reviewed_at: '2026-09-19T20:00:00+09:00',
        review_provider: { actual_provider_id: 'aip_x', actual_provider_name: GOOD_EFFECTIVE_PROVIDER },
      }],
    })
    const section = mergedSection(wrapper)
    const provider = section.find('.dip-ai-comment-toggle .dip-ai-provider')
    expect(provider.exists()).toBe(true)
    expect(provider.text()).toBe(`AI · ${GOOD_EFFECTIVE_PROVIDER}`)
  })

  it('draws no provider badge on the review card when review_provider is absent (legacy row)', () => {
    const wrapper = mountPanel({
      reviews: [{ id: 1, verdict: 'pass', finding_count: 0, comment: '문제 없음', reviewed_at: '2026-09-19T20:00:00+09:00' }],
    })
    const section = mergedSection(wrapper)
    expect(section.find('.dip-ai-comment-toggle .dip-ai-provider').exists()).toBe(false)
  })

  it('an automatic (review_id) rejection names the reviewing AI, never issued_to', () => {
    const wrapper = mountPanel({
      rejectionHistory: [{
        reason: '수정 필요', rejected_at: '2026-09-19T20:00:00+09:00',
        rejected_by: ISSUED_TO_SENTINEL,
        review_id: 42,
        rejection_provider: { ai_run_id: 'aiv_1', ai_provider_id: 'aip_x', ai_provider_name: GOOD_EFFECTIVE_PROVIDER },
      }],
    })
    const section = mergedSection(wrapper)
    const author = section.find('.dip-reject-quote-author')
    expect(author.text()).toBe(`AI · ${GOOD_EFFECTIVE_PROVIDER}`)
    expect(author.text()).not.toContain(ISSUED_TO_SENTINEL)
    expect(section.text()).not.toContain(ISSUED_TO_SENTINEL)
  })

  it('an automatic rejection with no resolvable provider shows the explicit unknown label, not issued_to', () => {
    const wrapper = mountPanel({
      rejectionHistory: [{
        reason: '수정 필요', rejected_at: '2026-09-19T20:00:00+09:00',
        rejected_by: ISSUED_TO_SENTINEL,
        review_id: 42,
        rejection_provider: null,
      }],
    })
    const section = mergedSection(wrapper)
    const author = section.find('.dip-reject-quote-author')
    expect(author.text()).toBe(`AI · ${i18n.global.t('main.doc_info_panel.ai_provider_unknown')}`)
    expect(author.text()).not.toContain(ISSUED_TO_SENTINEL)
  })

  it('a human rejection (no review_id) is unaffected — still shows the human author, no "AI ·" badge', () => {
    const wrapper = mountPanel({
      rejectionHistory: [{
        reason: '수정 필요', rejected_at: '2026-09-19T20:00:00+09:00',
        rejected_by: null,
      }],
    })
    const section = mergedSection(wrapper)
    const author = section.find('.dip-reject-quote-author')
    expect(author.text()).not.toContain('AI ·')
    expect(author.text()).toBe(i18n.global.t('main.doc_info_panel.rejection_review_author'))
  })

  it('a rework response shows ITS OWN provider, independent of (and possibly different from) the review', () => {
    const wrapper = mountPanel({
      rejectionHistory: [{
        reason: '수정 필요', rejected_at: '2026-09-19T20:00:00+09:00',
        rejected_by: ISSUED_TO_SENTINEL,
        review_id: 42,
        rejection_provider: { ai_run_id: 'aiv_1', ai_provider_id: 'aip_x', ai_provider_name: GOOD_EFFECTIVE_PROVIDER },
        ai_response: '반려 사유를 반영해 수정했습니다.',
        responded_at: '2026-09-19T21:00:00+09:00',
        response_provider: { ai_run_id: 'aiv_2', ai_provider_id: 'aip_y', ai_provider_name: REWORK_PROVIDER },
      }],
    })
    const section = mergedSection(wrapper)
    const responseLabel = section.find('.dip-ai-response-label .dip-ai-provider')
    expect(responseLabel.exists()).toBe(true)
    expect(responseLabel.text()).toBe(`AI · ${REWORK_PROVIDER}`)
    // the two badges disagree on purpose — the reviewer and the reworker are not the same run.
    const rejectAuthor = section.find('.dip-reject-quote-author')
    expect(rejectAuthor.text()).toContain(GOOD_EFFECTIVE_PROVIDER)
    expect(responseLabel.text()).not.toContain(GOOD_EFFECTIVE_PROVIDER)
  })
})

// 0582 TR0006 rev1 §3-D: the default Q card (qaFeedVisible) must show the same
// 'AI · {provider}' / explicit unknown-label convention for the AI that ASKED a
// question — not only in QaHistoryDialog's full [전체보기] view.
describe('DocInfoPanel Q card asker provider badge (0582 TR0006 rev1)', () => {
  function qCard(wrapper: ReturnType<typeof mountPanel>) {
    return wrapper.find('.dip-qa-card')
  }

  it('an in-app AI-authored question shows "AI · {provider}" from asker_provider', async () => {
    const wrapper = mountPanel({
      qaItems: [{
        id: 1, seq: 1, title: '범위 질문', body: '이 범위가 맞나요?',
        asker_kind: 'ai', answer_count: 0, answers: [],
        asker_provider: { ai_run_id: 'aiv_q1', ai_provider_id: 'aip_x', ai_provider_name: GOOD_EFFECTIVE_PROVIDER },
      }],
    })
    await flushPromises()
    const card = qCard(wrapper)
    expect(card.exists()).toBe(true)
    const badge = card.find('.dip-ai-provider')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe(`AI · ${GOOD_EFFECTIVE_PROVIDER}`)
  })

  it('an AI question with no resolvable provider shows the explicit unknown label', async () => {
    const wrapper = mountPanel({
      qaItems: [{
        id: 2, seq: 1, title: '범위 질문', body: '이 범위가 맞나요?',
        asker_kind: 'ai', answer_count: 0, answers: [],
        asker_provider: null,
      }],
    })
    await flushPromises()
    const badge = qCard(wrapper).find('.dip-ai-provider')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe(`AI · ${i18n.global.t('main.doc_info_panel.ai_provider_unknown')}`)
  })

  it('a legacy AI question (no asker_provider key at all) still shows the unknown label, not silence', async () => {
    const wrapper = mountPanel({
      qaItems: [{
        id: 3, seq: 1, title: '범위 질문', body: '이 범위가 맞나요?',
        asker_kind: 'ai', answer_count: 0, answers: [],
      }],
    })
    await flushPromises()
    const badge = qCard(wrapper).find('.dip-ai-provider')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe(`AI · ${i18n.global.t('main.doc_info_panel.ai_provider_unknown')}`)
  })

  it('a human question shows no "AI ·" badge at all', async () => {
    const wrapper = mountPanel({
      qaItems: [{
        id: 4, seq: 1, title: '휴먼 질문', body: '사람이 물어봄',
        asker_kind: 'human', answer_count: 0, answers: [],
      }],
    })
    await flushPromises()
    const card = qCard(wrapper)
    expect(card.exists()).toBe(true)
    expect(card.find('.dip-ai-provider').exists()).toBe(false)
  })
})
