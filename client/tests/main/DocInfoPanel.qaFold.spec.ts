import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// group 0126 + 0311 T0004 rev1: 질의 is its own section again (the merge partner was
// wrong — reject belongs with ai_review). rev3 반려("현재 적용되어있는 스타일을 전혀
// 사용하지 않는다"): the card itself is the one this panel already had — the amber
// .dip-qa-card with its title, body preview, option preview and [답변] action. The only
// thing kept from rev0 is NR0003 §5-3's cap: at most 3 cards, with the uncut text living
// in the full view. rev5 반려 §3·§4: the cap is silent now — no "이전 항목 N건 더" line —
// and the headline's [전체보기] is the only way out of the section.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const LONG = Array.from({ length: 12 }, (_, i) => `긴 본문 줄 ${i + 1}`).join('\n')

function qaResponse(overrides?: any) {
  return {
    data: {
      qa: {
        items: overrides ?? [
          {
            id: 1,
            seq: 1,
            title: '질문 제목',
            body: LONG,
            asker_kind: 'ai',
            answer_count: 1,
            answers: [{ body: LONG, author_kind: 'human' }],
          },
        ],
      },
    },
  }
}

function mountPanel() {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0075.0002-T',
      typeCode: 'T',
      reviewStatus: 'wf_in_progress',
      rejectReason: null,
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

// The qa section is found by its title — it has no bespoke wrapper class.
function qaSection(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.dip-section').find((s) => s.find('.dip-qa-headline').exists())!
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue(qaResponse())
})

afterEach(() => {
  // QaReviewHistoryDialog teleports to <body>; clear any leaked modal between tests.
  document.body.querySelectorAll('.modal-qhd').forEach((n) => n.closest('.modal-bg')?.remove())
})

describe('DocInfoPanel Q&A card + 전체보기 dialog (group 0126 / C안, 0311 T0004 rev1)', () => {
  it('keeps the panel\'s existing amber query card, answered accent included', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const card = wrapper.find('.dip-qa-card')
    expect(card.exists()).toBe(true)
    expect(card.classes()).toContain('answered-card')
    expect(card.find('.dip-qa-card-title').text()).toContain('질문 제목')
    // the card keeps its clamped body preview — rev0 had dropped it
    expect(card.find('.dip-qa-card-body').exists()).toBe(true)
    // the [답변] action is the card's existing primary mini-action
    expect(card.find('.dip-qa-card-actions .mini-action.primary').exists()).toBe(true)
    // the panel renders no inline fold boxes — reading happens in the dialog
    expect(wrapper.findAll('.dip-qa-fold').length).toBe(0)
    // rev1: a query is never rendered as a reject/review card — that merge was undone.
    // rev3: and no invented card family survives anywhere in the panel.
    expect(wrapper.findAll('.dip-mix-card').length).toBe(0)
    expect(wrapper.findAll('.dip-qa-badge').length).toBe(0)
  })

  it('caps the panel at 3 cards, with no "이전 항목 N건 더" line (rev5 §3·§4)', async () => {
    // NR0003 §5-3's actual recommendation, kept from rev0: the qa list is what made the
    // panel "터지" (it used to render every open query), so the cap stays even though
    // the section is standalone again. rev5 반려 §3: the overflow is no longer announced
    // with a link under the cards — the headline's [전체보기] is the only door.
    getRequest.mockResolvedValue(qaResponse(
      Array.from({ length: 5 }, (_, i) => ({
        id: i + 1, seq: i + 1, title: `질문 ${i + 1}`, body: LONG,
        asker_kind: 'ai', answer_count: 0, answers: [],
      })),
    ))
    const wrapper = mountPanel()
    await flushPromises()

    const section = qaSection(wrapper)
    expect(section.findAll('.dip-qa-card').length).toBe(3)
    // newest seq first
    expect(section.findAll('.dip-qa-card-title')[0].text()).toContain('질문 5')
    // the "이전 항목 2건 더 — 전체보기에서 확인" line is gone
    expect(section.find('.dip-ai-history-link').exists()).toBe(false)
    expect(section.text()).not.toContain('이전 항목')

    // the headline [전체보기] is what opens the full view now
    const more = section.find('.dip-qa-fullview')
    expect(more.exists()).toBe(true)
    await more.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.modal-qhd')).toBeTruthy()
    wrapper.unmount()
  })

  it('headline [전체보기] opens the merged dialog with the complete question and answer text', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.dip-qa-fullview').trigger('click') // [전체보기]
    await flushPromises()

    const modal = document.body.querySelector('.modal-qhd')
    expect(modal).toBeTruthy()
    // both the full question and the full answer text reach the modal uncut (no clamp),
    // in the query card the standalone full view already used.
    expect(modal!.textContent).toContain('긴 본문 줄 12')
    const entry = document.body.querySelector('.qhd-entry')
    expect(entry?.querySelector('.qhd-box')?.textContent).toContain('긴 본문 줄 12')
    expect(entry?.querySelector('.qhd-answer')?.textContent).toContain('긴 본문 줄 12')

    wrapper.unmount()
  })

  it('card [답변] opens the dialog with that query\'s inline answer form ready', async () => {
    // unanswered query so the answer form can open on the focused card
    getRequest.mockResolvedValue(qaResponse([
      { id: 3, seq: 1, title: '미응답 질문', body: LONG, asker_kind: 'ai', answer_count: 0, answers: [] },
    ]))
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.dip-qa-card-actions .mini-action').trigger('click') // [답변]
    await flushPromises()

    // the dialog opened with the inline answer box shown for that query
    expect(document.body.querySelector('.modal-qhd')).toBeTruthy()
    expect(document.body.querySelector('.qhd-answer-textarea')).toBeTruthy()

    wrapper.unmount()
  })
})
