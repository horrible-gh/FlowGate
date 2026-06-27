import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// group 0126: the 질의 응답 section follows the prototype card layout — the panel shows
// a compact card (title + 2-line preview) that never stretches the side panel, and each
// side-card exposes only the [답변] action. The complete text reaches the dialog uncut.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const LONG = Array.from({ length: 12 }, (_, i) => `긴 본문 줄 ${i + 1}`).join('\n')

function qaResponse() {
  return {
    data: {
      qa: {
        items: [
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

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue(qaResponse())
})

afterEach(() => {
  // QaHistoryDialog teleports to <body>; clear any leaked modal between tests.
  document.body.querySelectorAll('.modal-qhd').forEach((n) => n.closest('.modal-bg')?.remove())
})

describe('DocInfoPanel Q&A card + 전체 보기 dialog (group 0126 / C안)', () => {
  it('shows a compact card (title + clamped preview), not the full body, in the panel', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const card = wrapper.find('.dip-qa-card')
    expect(card.exists()).toBe(true)
    // the card title carries the seq + title; the preview is the body (CSS clamps it to 2 lines)
    expect(card.find('.dip-qa-card-title').text()).toContain('질문 제목')
    expect(card.find('.dip-qa-card-body').exists()).toBe(true)
    // the panel no longer renders inline fold boxes — reading happens in the dialog
    expect(wrapper.findAll('.dip-qa-fold').length).toBe(0)
    expect(card.findAll('.dip-qa-card-actions .mini-action').length).toBe(1)
    expect(card.find('.dip-qa-card-actions .mini-action').text()).toContain(i18n.global.t('main.doc_info_panel.qa_answer'))
  })

  it('card [답변] opens the full Q&A dialog with the complete question and answer text', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.dip-qa-card-actions .mini-action.primary').trigger('click') // [답변]
    await flushPromises()

    const modal = document.body.querySelector('.modal-qhd')
    expect(modal).toBeTruthy()
    // both the full question and the full answer text reach the modal uncut
    const boxes = Array.from(document.body.querySelectorAll('.qhd-box')).map((b) => b.textContent ?? '')
    expect(boxes.length).toBe(2)
    expect(boxes.every((txt) => txt.includes('긴 본문 줄 12'))).toBe(true)

    wrapper.unmount()
  })

  it('card [답변] opens the dialog with that query\'s inline answer form ready', async () => {
    // unanswered query so the answer form can open
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 3, seq: 1, title: '미응답 질문', body: LONG, asker_kind: 'ai', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.dip-qa-card-actions .mini-action.primary').trigger('click') // [답변]
    await flushPromises()

    // the dialog opened with the inline answer textarea shown for that query
    expect(document.body.querySelector('.modal-qhd')).toBeTruthy()
    expect(document.body.querySelector('.qhd-answer-textarea')).toBeTruthy()

    wrapper.unmount()
  })
})
