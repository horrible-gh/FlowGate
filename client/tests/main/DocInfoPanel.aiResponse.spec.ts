// P0005/T0006: the AI response arrives with the inbox re-submission and is shown
// (read-only) threaded under its rejection. There is no in-app input.
//
// 0311 T0004 rev1: 반려 and AI검수 are one merged section now (rev0 had merged reject
// with 질의 — the wrong pair). T0004 rev1 §2 is explicit that NEITHER side may lose a
// feature in the merge, so this file pins both halves of the reject card: the AI
// response thread stays IN THE PANEL (folded, one per rejection, a sibling of the
// reason — not nested inside it), and the merged QaReviewHistoryDialog still lists
// every rejection with its own response.
//
// rev3 반려("현재 적용되어있는 스타일을 전혀 사용하지 않는다"): the merge no longer
// invents a card family. Each rejection is the panel's existing .dip-reject-quote and
// each review is the existing .dip-ai-entry, so this spec asserts against those — a
// regression here means the merge grew its own markup again.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import type { AiReview } from '@main/types/aiReview'

type RejItem = {
  rejection_id?: string
  reason: string
  rejected_at: string
  rejected_by: string | null
  ai_response?: string | null
  responded_at?: string | null
  response_revision_no?: number | null
}

function mountPanel(history: RejItem[], reviews: AiReview[] = []) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0014.0002-D',
      typeCode: 'D',
      reviewStatus: 'rejected',
      rejectReason: history[history.length - 1]?.reason ?? null,
      rejectionHistory: history,
      aiReview: reviews[reviews.length - 1] ?? null,
      aiReviewHistory: reviews,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

// The merged section is identified by its own title, not by a bespoke wrapper class —
// there is no wrapper class any more.
function mergedSection(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.dip-section')
    .find((s) => s.find('.dip-sec-toggle').exists() && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject')))!
}

// rev5 반려 §3: the section's door to the full history is the [전체보기] button on the
// right of the 「AI 검수·반려」 headline — the "이전 항목 N건 더" line below the cards is gone.
async function openMergedDialog(wrapper: ReturnType<typeof mountPanel>) {
  await mergedSection(wrapper).find('.dip-rr-fullview').trigger('click')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel AI response inside the merged AI검수·반려 section (0311 T0004 rev1)', () => {
  it('threads the AI response under its own rejection card, folded by default', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '해당 부분을 수정했습니다.', responded_at: '2026-06-12T21:10:00+09:00' },
    ])
    const entry = wrapper.find('.dip-rr-entry')
    expect(entry.exists()).toBe(true)
    expect(entry.find('.dip-reject-quote').exists()).toBe(true)

    const box = entry.find('.dip-ai-response')
    expect(box.exists()).toBe(true)
    // folded by default (the body is clamped by CSS, the text is in the DOM either way)
    expect(box.classes()).not.toContain('open')
    expect(box.find('.dip-ai-response-head').attributes('aria-expanded')).toBe('false')
    expect(box.find('.dip-ai-response-body').text()).toContain('해당 부분을 수정했습니다.')

    await entry.find('.dip-ai-response-head').trigger('click')
    expect(wrapper.find('.dip-rr-entry .dip-ai-response').classes()).toContain('open')
  })

  it('renders the AI response OUTSIDE the rejection quote, as a sibling', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '수정했습니다.' },
    ])
    const entry = wrapper.find('.dip-rr-entry').element
    expect(entry.querySelector('.dip-ai-response')).toBeTruthy()
    // no double box: the response is NOT inside the quote (which is the only box).
    expect(entry.querySelector('.dip-reject-quote .dip-ai-response')).toBeFalsy()
  })

  it('renders no response box when no AI response was submitted', () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: null },
    ])
    expect(wrapper.find('.dip-rr-entry .dip-reject-quote').exists()).toBe(true)
    expect(wrapper.find('.dip-ai-response').exists()).toBe(false)
  })

  it('folds each rejection card independently — opening one does not open the other', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_old', reason: '예전', rejected_at: '2026-06-12T20:00:00+09:00', rejected_by: null,
        ai_response: '예전 대응' },
      { rejection_id: 'rej_new', reason: '최신 사유', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '최신 대응' },
    ])
    expect(wrapper.findAll('.dip-ai-response').length).toBe(2)

    await wrapper.findAll('.dip-ai-response-head')[0].trigger('click')
    const boxes = wrapper.findAll('.dip-ai-response')
    expect(boxes[0].classes()).toContain('open')
    expect(boxes[1].classes()).not.toContain('open')
  })

  it('folds each rejection reason independently, using the existing quote toggle', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_old', reason: '예전', rejected_at: '2026-06-12T20:00:00+09:00', rejected_by: null },
      { rejection_id: 'rej_new', reason: '최신 사유', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null },
    ])
    const quotes = wrapper.findAll('.dip-reject-quote')
    expect(quotes.length).toBe(2)
    expect(quotes[0].classes()).not.toContain('open')

    await quotes[0].find('.dip-reject-quote-toggle').trigger('click')
    const after = wrapper.findAll('.dip-reject-quote')
    expect(after[0].classes()).toContain('open')
    expect(after[1].classes()).not.toContain('open')
  })
})

describe('DocInfoPanel merged AI검수·반려 feed (0311 T0004 rev1 §2)', () => {
  const REVIEW: AiReview = {
    id: 11,
    reviewer_name: '검수봇',
    verdict: 'issues',
    finding_count: 1,
    findings: [{ locus: 'app.py:12', note: '널 검사가 없다' }],
    comment: '전반적으로는 통과 가능',
    reviewed_at: '2026-06-12T20:50:00+09:00',
  }

  it('puts the AI검수 card and the 반려 card in ONE section, ordered by real time', () => {
    const wrapper = mountPanel(
      [{ rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null }],
      [REVIEW],
    )
    const section = mergedSection(wrapper)
    expect(section).toBeTruthy()
    // 반려 사유 / AI 검수 의견 as separate section headers are gone — one header covers both.
    expect(wrapper.findAll('.dip-section').filter((s) => s.find('.dip-sec-toggle').exists()
      && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject'))).length).toBe(1)

    const entries = section.findAll('.dip-rr-entry')
    expect(entries.length).toBe(2)
    // 20:50 review is newer than the 20:43 rejection — this feed sorts by the actual
    // timestamps both sides carry (rejected_at / reviewed_at), nothing is invented.
    expect(entries[0].find('.dip-ai-entry').exists()).toBe(true)
    expect(entries[1].find('.dip-reject-quote').exists()).toBe(true)
  })

  it('renders each half with the markup that surface already had (no new card family)', () => {
    const wrapper = mountPanel(
      [{ rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null }],
      [REVIEW],
    )
    // 반려: the quote box with its author/date toggle head and the clamped reason.
    const quote = wrapper.find('.dip-reject-quote')
    expect(quote.find('.dip-reject-quote-toggle').exists()).toBe(true)
    expect(quote.find('.dip-reject-quote-author').exists()).toBe(true)
    expect(quote.find('.dip-reject-date').text()).toContain('20:43')
    expect(quote.find('.dip-reject-quote-body .dip-reject-reason').text()).toBe('고쳐주세요')
    // AI검수: the bare entry — the box the reviewer said must NOT be doubled. rev5 §1:
    // what is left inside it is the 「검수 의견」 fold, with no head/badge/findings.
    expect(wrapper.find('.dip-ai-entry').exists()).toBe(true)
    expect(wrapper.find('.dip-ai-entry .dip-ai-comment').exists()).toBe(true)
    expect(wrapper.find('.dip-ai-entry-head').exists()).toBe(false)
    // nothing from rev0's invented family survives
    expect(wrapper.find('.dip-mix-card').exists()).toBe(false)
    expect(wrapper.find('.dip-mix-badge').exists()).toBe(false)
    expect(wrapper.find('.dip-mix-feed').exists()).toBe(false)
  })

  it('leaves the AI검수 card with the verdict badge inside the 「검수 의견」 toggle (rev5 §1·§2 / 0422 TR0003 rev2)', async () => {
    const wrapper = mountPanel([], [REVIEW])
    const card = wrapper.find('.dip-ai-entry')
    expect(card.exists()).toBe(true)

    // 반려 §1: the head line (시각 · AI) and the findings list do not appear in the panel
    // any more — they live in [전체보기]. 0422 TR0003 rev2 places the verdict badge in
    // the surviving 검수의견 toggle header itself.
    expect(card.find('.dip-ai-meta').exists()).toBe(false)
    expect(card.find('.dip-ai-findings').exists()).toBe(false)
    expect(card.text()).not.toContain('검수봇')
    expect(card.text()).not.toContain('널 검사가 없다')
    expect(card.find('.dip-ai-comment-toggle .dip-ai-verdict').exists()).toBe(true)
    expect(card.find('.dip-ai-comment-toggle .dip-ai-verdict').text()).toBe(i18n.global.t('main.doc_info_panel.ai_verdict_issues', { n: 1 }))
    expect(Array.from(card.element.children).some((el) => el.classList.contains('dip-ai-verdict'))).toBe(false)

    // what remains is the comment fold, labelled 「검수 의견」 (§2), folded by default
    const comment = wrapper.find('.dip-ai-comment')
    expect(comment.find('.dip-ai-comment-label').text()).toContain(i18n.global.t('main.doc_info_panel.ai_comment_label'))
    expect(comment.classes()).not.toContain('open')
    await comment.find('.dip-ai-comment-toggle').trigger('click')
    expect(wrapper.find('.dip-ai-comment').classes()).toContain('open')
    expect(wrapper.find('.dip-ai-comment-body').text()).toContain('전반적으로는 통과 가능')
  })

  it('labels a rejection card 「반려」 when the rejecter has no display name (rev5 §2)', () => {
    const wrapper = mountPanel(
      [{ rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null }],
    )
    // the suite runs in the default locale, so pin the Korean wording on the ko bundle
    const ko = (i18n.global.getLocaleMessage('ko') as any).main.doc_info_panel
    expect(ko.rejection_review_author).toBe('반려')
    expect(ko.rejection_review_author).not.toBe('검수 의견')
    expect(wrapper.find('.dip-reject-quote-author').text())
      .toContain(i18n.global.t('main.doc_info_panel.rejection_review_author'))
  })

  it('shows the empty state only when there is neither a review nor a rejection', () => {
    const wrapper = mountPanel([], [])
    const section = mergedSection(wrapper)
    expect(section.find('.dip-rr-entry').exists()).toBe(false)
    expect(section.find('.dip-reject-empty').exists()).toBe(true)
  })

  it('caps the merged feed at 3 cards, with [전체보기] on the headline (rev5 §3·§4)', async () => {
    const wrapper = mountPanel(
      Array.from({ length: 5 }, (_, i) => ({
        rejection_id: `rej_${i}`,
        reason: `사유 ${i}`,
        rejected_at: `2026-06-12T20:0${i}:00+09:00`,
        rejected_by: null,
      })),
    )
    const section = mergedSection(wrapper)
    expect(section.findAll('.dip-rr-entry').length).toBe(3)
    // newest first
    expect(section.findAll('.dip-reject-reason')[0].text()).toBe('사유 4')
    // §3: the "이전 항목 N건 더 — 전체보기에서 확인" line is gone from the section body …
    expect(section.find('.dip-sec-body .dip-ai-history-link').exists()).toBe(false)
    expect(section.text()).not.toContain('이전 항목')
    // … and [전체보기] sits on the right of the headline instead, opening the same dialog
    const full = section.find('.dip-qa-headline .dip-rr-fullview')
    expect(full.exists()).toBe(true)
    expect(full.text()).toContain(i18n.global.t('main.doc_info_panel.qa_view_full'))
    await full.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.modal-qhd')).toBeTruthy()
    wrapper.unmount()
  })
})

describe('QaReviewHistoryDialog still carries the full rejection history (T0004 rev1 §3)', () => {
  it('lists EVERY rejection with its own response, not just the latest', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_old', reason: '예전', rejected_at: '2026-06-12T20:00:00+09:00', rejected_by: null,
        ai_response: '예전 대응' },
      { rejection_id: 'rej_new', reason: '최신 사유', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '최신 대응' },
    ])
    await openMergedDialog(wrapper)

    const entries = Array.from(document.body.querySelectorAll('.rhd-entry'))
    expect(entries.length).toBe(2)
    const bodyText = entries.map((e) => e.textContent ?? '').join(' | ')
    expect(bodyText).toContain('예전 대응')
    expect(bodyText).toContain('최신 대응')
    wrapper.unmount()
  })

  it('keeps the response OUTSIDE the rejection card in the dialog too', async () => {
    const wrapper = mountPanel([
      { rejection_id: 'rej_1', reason: '고쳐주세요', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
        ai_response: '수정했습니다.' },
    ])
    await openMergedDialog(wrapper)

    const entry = document.body.querySelector('.rhd-entry')!
    expect(entry.querySelector('.rhd-item--reject')).toBeTruthy()
    expect(entry.querySelector('.rhd-ai-response')).toBeTruthy()
    expect(entry.querySelector('.rhd-item--reject .rhd-ai-response')).toBeFalsy()
    wrapper.unmount()
  })
})
