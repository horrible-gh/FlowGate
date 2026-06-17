import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import ReviewHistoryDialog from '@main/components/ReviewHistoryDialog.vue'

// P0005/T0006: each rejection entry pairs the reason with its AI response.
// Reviewer #8: the response must NOT be nested inside the rejection box — it is a
// reply below the card, paired by sharing the same timeline entry. teleport is
// stubbed so the dialog renders inline within the wrapper (no document.body
// leakage between tests).

function mountDialog(rejections: any[]) {
  return mount(ReviewHistoryDialog, {
    props: { visible: true, reviews: [], rejections },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

const REJ = {
  rejection_id: 'rej_1', reason: '반려 사유 1', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null,
  ai_response: '대응 내용 1', responded_at: '2026-06-12T21:00:00+09:00',
}

describe('ReviewHistoryDialog AI response (P0005/T0006)', () => {
  it('renders the AI response outside the rejection box, in the same timeline entry', () => {
    const wrapper = mountDialog([REJ])
    const entry = wrapper.find('.rhd-entry')
    expect(entry.exists()).toBe(true)
    // Paired: card and response share the entry…
    expect(entry.find('.rhd-item--reject').exists()).toBe(true)
    const resp = entry.find('.rhd-ai-response')
    expect(resp.exists()).toBe(true)
    expect(resp.text()).toContain('대응 내용 1')
    // …but the response is NOT inside the rejection box (reviewer #8).
    expect(entry.find('.rhd-item--reject .rhd-ai-response').exists()).toBe(false)
  })

  // R0001: the AI response must show its own timestamp in the full view.
  it('renders the AI response timestamp (responded_at)', () => {
    const wrapper = mountDialog([REJ])
    const date = wrapper.find('.rhd-ai-response-date')
    expect(date.exists()).toBe(true)
    // 2026-06-12T21:00:00+09:00 → "2026-06-12 21:00" (local formatWhen)
    expect(date.text()).toContain('21:00')
  })

  // R0001 rev1: the clock must sit at the right edge (date immediately before the
  // chevron), not stranded mid-row. Order within the head: label, date, chevron.
  it('places the timestamp at the right edge, immediately before the chevron', () => {
    const wrapper = mountDialog([REJ])
    const head = wrapper.find('.rhd-ai-response-head')
    const kids = Array.from(head.element.children).map((el) => el.className)
    const dateIdx = kids.findIndex((c) => c.includes('rhd-ai-response-date'))
    const chevronIdx = kids.findIndex((c) => c.includes('rhd-ai-chevron'))
    expect(dateIdx).toBeGreaterThan(-1)
    expect(chevronIdx).toBe(dateIdx + 1)
    // chevron is the last child (nothing strands to its right)
    expect(chevronIdx).toBe(kids.length - 1)
  })

  it('omits the AI response timestamp when responded_at is absent', () => {
    const wrapper = mountDialog([{ ...REJ, rejection_id: 'rej_3', responded_at: null }])
    expect(wrapper.find('.rhd-ai-response').exists()).toBe(true)
    expect(wrapper.find('.rhd-ai-response-date').exists()).toBe(false)
  })

  it('omits the AI response block when there is no response', () => {
    const wrapper = mountDialog([{ ...REJ, rejection_id: 'rej_2', ai_response: null, responded_at: null }])
    expect(wrapper.find('.rhd-ai-response').exists()).toBe(false)
  })

  // Reviewer #8: "열면 기본 펴기" — everything starts unfolded when the dialog opens.
  it('starts with the rejection reason unfolded and folds it via the head toggle', async () => {
    const wrapper = mountDialog([REJ])
    const reason = wrapper.find('.rhd-reject-reason')
    expect(reason.classes()).not.toContain('collapsed')
    expect(wrapper.find('.rhd-fold').attributes('aria-expanded')).toBe('true')
    await wrapper.find('.rhd-fold').trigger('click')
    expect(wrapper.find('.rhd-reject-reason').classes()).toContain('collapsed')
    expect(wrapper.find('.rhd-fold').attributes('aria-expanded')).toBe('false')
  })

  it('starts with the AI response unfolded and folds it via its head toggle', async () => {
    const wrapper = mountDialog([REJ])
    expect(wrapper.find('.rhd-ai-response').classes()).toContain('open')
    expect(wrapper.find('.rhd-ai-response-head').attributes('aria-expanded')).toBe('true')
    await wrapper.find('.rhd-ai-response-head').trigger('click')
    expect(wrapper.find('.rhd-ai-response').classes()).not.toContain('open')
    expect(wrapper.find('.rhd-ai-response-head').attributes('aria-expanded')).toBe('false')
  })

  it('resets folded state back to unfolded when the dialog reopens', async () => {
    const wrapper = mountDialog([REJ])
    await wrapper.find('.rhd-fold').trigger('click')
    await wrapper.find('.rhd-ai-response-head').trigger('click')
    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    expect(wrapper.find('.rhd-reject-reason').classes()).not.toContain('collapsed')
    expect(wrapper.find('.rhd-ai-response').classes()).toContain('open')
  })
})
