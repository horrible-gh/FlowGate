import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// R0075 (group 0075, rev1): the 질의 응답 section's question/answer bodies must not stretch
// the side panel, AND reading a query must take a single disclosure — expanding the Q item
// shows the question/answer bodies directly (no nested fold to click). Long text scrolls
// inside a height-capped box, and a [전체 보기] modal still shows the full text uncut.

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

describe('DocInfoPanel Q&A single-disclosure + 전체 보기 (R0075 rev1)', () => {
  it('shows the question and answer bodies directly when the Q item is expanded (single click, no nested toggle)', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // a single disclosure — expanding the Q item — already reveals the bodies
    await wrapper.find('.dip-qa-item-head').trigger('click')

    const folds = wrapper.findAll('.dip-qa-fold')
    expect(folds.length).toBe(2) // one question box + one answer box
    // no inner fold toggle remains — the second click is gone
    expect(wrapper.findAll('.dip-qa-fold-toggle').length).toBe(0)

    // the full body text is present immediately (rendered in a height-capped scroll box)
    const bodies = wrapper.findAll('.dip-qa-fold-body').map((b) => b.text())
    expect(bodies.length).toBe(2)
    expect(bodies.every((txt) => txt.includes('긴 본문 줄 1') && txt.includes('긴 본문 줄 12'))).toBe(true)

    // the answer box carries the green-tone modifier; the question box does not
    expect(wrapper.findAll('.dip-qa-fold--answer').length).toBe(1)
  })

  it('keeps the bodies hidden until the Q item itself is expanded', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // before expanding the item, no body boxes are rendered (panel stays compact)
    expect(wrapper.findAll('.dip-qa-fold').length).toBe(0)
    expect(wrapper.find('.dip-qa-item-head').exists()).toBe(true)
  })

  it('offers [전체 보기] that opens the full Q&A modal with the complete text', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.dip-qa-item-head').trigger('click')

    const fullView = wrapper.find('.dip-ai-history-link')
    expect(fullView.exists()).toBe(true)

    await fullView.trigger('click')
    await flushPromises()

    const modal = document.body.querySelector('.modal-qhd')
    expect(modal).toBeTruthy()
    // both the full question and the full answer text reach the modal uncut
    const boxes = Array.from(document.body.querySelectorAll('.qhd-box')).map((b) => b.textContent ?? '')
    expect(boxes.length).toBe(2)
    expect(boxes.every((txt) => txt.includes('긴 본문 줄 12'))).toBe(true)

    wrapper.unmount()
  })
})
