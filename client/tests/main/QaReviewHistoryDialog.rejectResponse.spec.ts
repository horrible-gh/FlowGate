import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import QaReviewHistoryDialog from '@main/components/QaReviewHistoryDialog.vue'

// P0005/T0006: each rejection entry pairs the reason with its AI response.
// Reviewer #8: the response must NOT be nested inside the rejection box — it is a
// reply below the card, paired by sharing the same timeline entry. Reviewer #7: the
// card is the ONLY rejection box (no double box).
//
// 0311 T0004 merged ReviewHistoryDialog's reject/ai_response rendering into
// QaReviewHistoryDialog. rev3 반려("현재 적용되어있는 스타일을 전혀 사용하지 않는다"):
// the merge no longer redraws these entries in a new card family — it keeps the
// ReviewHistoryDialog markup verbatim, INCLUDING the fold controls (.rhd-fold for the
// reason, .rhd-ai-response-head for the response) that rev0 had silently dropped.
// teleport is stubbed so the dialog renders inline within the wrapper (no
// document.body leakage between tests).

function mountDialog(rejections: any[]) {
  return mount(QaReviewHistoryDialog, {
    props: { visible: true, reviews: [], rejections },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

const REJ = {
  rejection_id: 'rej_1', reason: '반려 사유 1', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
  ai_response: '대응 내용 1', responded_at: '2026-06-12T21:00:00+09:00',
}

describe('QaReviewHistoryDialog reject AI response (P0005/T0006, merged 0311 T0004)', () => {
  it('renders the AI response outside the rejection card, in the same entry', () => {
    const wrapper = mountDialog([REJ])
    const entry = wrapper.find('.rhd-entry')
    expect(entry.exists()).toBe(true)
    // Paired: card and response share the entry…
    expect(entry.find('.rhd-item--reject').exists()).toBe(true)
    const resp = entry.find('.rhd-ai-response')
    expect(resp.exists()).toBe(true)
    expect(resp.text()).toContain('대응 내용 1')
    // …but the response is NOT nested inside the rejection card (reviewer #7/#8).
    expect(entry.find('.rhd-item--reject .rhd-ai-response').exists()).toBe(false)
  })

  // R0001: the AI response must show its own timestamp.
  it('renders the AI response timestamp (responded_at)', () => {
    const wrapper = mountDialog([REJ])
    const date = wrapper.find('.rhd-ai-response-date')
    expect(date.exists()).toBe(true)
    // 2026-06-12T21:00:00+09:00 → "2026-06-12 21:00" (local formatWhen)
    expect(date.text()).toContain('21:00')
  })

  it('omits the AI response timestamp when responded_at is absent', () => {
    const wrapper = mountDialog([{ ...REJ, rejection_id: 'rej_3', responded_at: null }])
    expect(wrapper.find('.rhd-ai-response').exists()).toBe(true)
    expect(wrapper.find('.rhd-ai-response-date').exists()).toBe(false)
  })

  it('omits the reply block when there is no response', () => {
    const wrapper = mountDialog([{ ...REJ, rejection_id: 'rej_2', ai_response: null, responded_at: null }])
    expect(wrapper.find('.rhd-ai-response').exists()).toBe(false)
  })

  it('lists every rejection as its own entry, each carrying its own response (no latest-only cap)', () => {
    const older = { ...REJ, rejection_id: 'rej_old', reason: '예전', rejected_at: '2026-06-12T20:00:00+09:00', ai_response: '예전 대응', responded_at: null }
    const wrapper = mountDialog([older, REJ])
    const entries = wrapper.findAll('.rhd-entry')
    expect(entries.length).toBe(2)
    // newest-first
    expect(entries[0].text()).toContain('대응 내용 1')
    expect(entries[1].text()).toContain('예전 대응')
  })

  it('keeps the reason and response fold controls the old dialog had, unfolded on open', async () => {
    const wrapper = mountDialog([REJ])
    expect(wrapper.find('.rhd-reject-reason').text()).toContain('반려 사유 1')

    // reason: unfolded by default, its chevron button folds it in place
    const fold = wrapper.find('.rhd-fold')
    expect(fold.exists()).toBe(true)
    expect(fold.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.rhd-reject-reason').classes()).not.toContain('collapsed')
    await fold.trigger('click')
    expect(wrapper.find('.rhd-reject-reason').classes()).toContain('collapsed')

    // response: same idiom, independent of the reason
    const head = wrapper.find('.rhd-ai-response-head')
    expect(head.exists()).toBe(true)
    expect(wrapper.find('.rhd-ai-response').classes()).toContain('open')
    await head.trigger('click')
    expect(wrapper.find('.rhd-ai-response').classes()).not.toContain('open')
  })

  it('shows who rejected it, which the standalone review dialog could not', () => {
    const wrapper = mount(QaReviewHistoryDialog, {
      props: {
        visible: true,
        reviews: [],
        rejections: [{ ...REJ, rejected_by: 'u-1' }],
        rejectedByDisplay: (id?: string | null) => (id === 'u-1' ? '검수자 김' : ''),
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    expect(wrapper.find('.rhd-who').text()).toBe('검수자 김')
  })
})
