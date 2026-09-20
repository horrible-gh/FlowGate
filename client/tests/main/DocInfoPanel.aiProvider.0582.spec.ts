// 0582 T0005 §4/§8.4/§8.5/§8.6: the compact AI검수·반려 section must show the ACTUAL
// AI provider — never `issued_to`/the request/current-setting provider — for the
// review card, an automatic rejection, and a rework response, each independently.
// 0582 T0009/TR0010 rev2: the Q card asker provider badge (formerly 0582 TR0006 rev1)
// was removed from the panel, so its dedicated tests are gone too.
import { mount } from '@vue/test-utils'
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

function mountPanel(opts: { reviews?: AiReview[]; rejectionHistory?: RejectionHistoryItem[] }) {
  const reviews = opts.reviews ?? []
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
