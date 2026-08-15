// 0311 T0004 rev1: the rejection reason lives in the merged 「AI 검수·반려」 section
// (rev0 had wrongly merged it with 질의 instead). rev3 반려("현재 적용되어있는 스타일을
// 전혀 사용하지 않는다 / 반려·대응이 그렇게 되어있던가"): merging did not change how a
// rejection is drawn — it is still the .dip-reject-quote box with its author/date toggle
// head and a 2-line clamped .dip-reject-reason, exactly as the standalone section had it.
// The clamp itself lives in shared/app.css, so the last case reads that file (jsdom does
// not compute layout, so -webkit-line-clamp can't be observed by mounting).
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

const LONG_REJECTION_REASON = [
  '최소 한글자 이상 넣을것',
  '',
  ...Array.from({ length: 12 }, () => '세로길이 테스트'),
].join('\n')

function mountPanel() {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0015.0002-D',
      typeCode: 'D',
      reviewStatus: 'rejected',
      rejectReason: LONG_REJECTION_REASON,
      rejectionHistory: [
        {
          reason: '최소 한글자 이상 넣을것',
          rejected_at: '2026-06-12T20:43:00+09:00',
          rejected_by: null,
        },
        {
          reason: LONG_REJECTION_REASON,
          rejected_at: '2026-06-12T20:47:12+09:00',
          rejected_by: null,
        },
      ],
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

describe('DocInfoPanel rejection reason height (0311 T0004 rev1 merged AI검수·반려)', () => {
  it('renders rejection quotes inside the merged section, newest first, clamped', () => {
    const wrapper = mountPanel()
    const section = mergedSection(wrapper)
    const quotes = section.findAll('.dip-reject-quote')

    expect(quotes.length).toBe(2)
    // sorted by the REAL timestamp, newest first (20:47:12 before 20:43:00)
    const reason = quotes[0].find('.dip-reject-quote-body .dip-reject-reason')
    expect(reason.text()).toContain('세로길이 테스트')
    // the full multiline text is present in the DOM — the clamp is CSS-only, not truncation
    expect(reason.text()).toBe(LONG_REJECTION_REASON)
    expect(quotes[1].find('.dip-reject-reason').text()).toBe('최소 한글자 이상 넣을것')
    // the quote keeps its own in-place fold control — merging did not take it away
    expect(quotes[0].find('.dip-reject-quote-toggle').exists()).toBe(true)
    expect(quotes[0].find('.dip-reject-date').text()).toContain('06-12')
    // and rev0's invented card family is gone
    expect(section.findAll('.dip-mix-card').length).toBe(0)
  })

  it('does not leak the rejection into the 질의 section', () => {
    const wrapper = mountPanel()
    const qaSection = wrapper.findAll('.dip-section').find((s) => s.find('.dip-qa-headline').exists())!
    expect(qaSection.exists()).toBe(true)
    expect(qaSection.text()).not.toContain('세로길이 테스트')
    expect(qaSection.findAll('.dip-reject-quote').length).toBe(0)
  })

  it('[전체 보기] opens the merged dialog where the full reason is also shown', async () => {
    const wrapper = mountPanel()
    await mergedSection(wrapper).find('.dip-rr-fullview').trigger('click')
    await wrapper.vm.$nextTick()

    const modalReason = document.body.querySelector('.rhd-item--reject .rhd-reject-reason')
    expect(modalReason?.textContent).toContain('세로길이 테스트')
    wrapper.unmount()
  })

  it('uses a two-line preview on the panel card (CSS clamp)', () => {
    // .dip-reject-reason is a shared style (shared/app.css) — the merged section reuses it
    // rather than declaring a clamp of its own.
    const css = readFileSync(join(process.cwd(), 'shared/app.css'), 'utf-8')
    const block = css.match(/\.dip-reject-reason\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(block).toMatch(/-webkit-line-clamp\s*:\s*2/)
    expect(block).toMatch(/overflow-y\s*:\s*hidden/)
  })

  it('clamps the OPEN reason too — ellipsis, not a scroll box (rev5 §4)', () => {
    // 반려 §4: "문자열이 너무 길면 ... 을 넣어라 어차피 전체보기에서 볼테니까". Opening a
    // rejection used to swap the clamp for an 8rem scroll box; it now just clamps wider,
    // so the text always ends in an ellipsis and the panel cannot grow past ~6 lines.
    const css = readFileSync(join(process.cwd(), 'shared/app.css'), 'utf-8')
    const open = css.match(/\.dip-reject-quote\.open \.dip-reject-reason\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(open).toMatch(/-webkit-line-clamp\s*:\s*6/)
    expect(open).not.toMatch(/overflow-y\s*:\s*auto/)
    // and nothing re-enables the scrollbar further down the file
    expect(css).not.toMatch(/\.dip-reject-quote\.open \.dip-reject-reason::-webkit-scrollbar/)
  })
})
