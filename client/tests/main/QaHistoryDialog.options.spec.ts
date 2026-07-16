import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import QaHistoryDialog from '@main/components/QaHistoryDialog.vue'

// group 0243 R0001 / D0006 §6: a query may carry reference options. They are shown with
// nothing preselected and no recommendation accent (the 0022 rule this extension does not
// reverse), the free-text box stays available regardless, and picking alone is a complete
// answer — the server fills the body with the chosen label.

function optionItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    seq: 1,
    title: '배포 방식',
    body: '어느 쪽으로 할까요?',
    asker_kind: 'ai',
    options: [
      { id: 'o1', label: 'A안: 무중단 배포' },
      { id: 'o2', label: 'B안: 점검창 배포' },
    ],
    answer_count: 0,
    answers: [],
    ...overrides,
  }
}

function mountDialog(props: Record<string, unknown> = {}, item = optionItem()) {
  return mount(QaHistoryDialog, {
    props: { visible: true, items: [item], ...props },
    global: { plugins: [i18n] },
  })
}

function openAnswerForm() {
  document.body.querySelectorAll<HTMLButtonElement>('.qhd-answer-actions .btn')[0].click()
  return flushPromises()
}

function optionButtons() {
  return Array.from(document.body.querySelectorAll<HTMLButtonElement>('.qhd-opt-btn'))
}

afterEach(() => {
  document.body.querySelectorAll('.modal-qhd').forEach((n) => n.closest('.modal-bg')?.remove())
})

describe('QaHistoryDialog query options (group 0243 R0001)', () => {
  it('renders each option unselected, with the free-text box still present', async () => {
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() })
    await flushPromises()
    await openAnswerForm()

    const buttons = optionButtons()
    expect(buttons.map((b) => b.textContent?.trim())).toEqual([
      'A안: 무중단 배포',
      'B안: 점검창 배포',
    ])
    // nothing preselected, nothing recommended (0022 rule)
    expect(buttons.some((b) => b.classList.contains('picked'))).toBe(false)
    // free-form answering is never taken away
    expect(document.body.querySelector('.qhd-answer-textarea')).toBeTruthy()
    wrapper.unmount()
  })

  it('submits the picked option id with an empty body (pick-only answer)', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()
    await openAnswerForm()

    optionButtons()[1].click()
    await flushPromises()
    expect(optionButtons()[1].classList.contains('picked')).toBe(true)

    document.body.querySelector<HTMLButtonElement>('.qhd-answer-form .btn-primary')!.click()
    await flushPromises()

    expect(submitAnswer).toHaveBeenCalledWith(7, '', ['o2'])
    wrapper.unmount()
  })

  it('submits body and pick together when both are given', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()
    await openAnswerForm()

    optionButtons()[0].click()
    const textarea = document.body.querySelector<HTMLTextAreaElement>('.qhd-answer-textarea')!
    textarea.value = 'A안이되 롤백 절차 추가'
    textarea.dispatchEvent(new Event('input'))
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('.qhd-answer-form .btn-primary')!.click()
    await flushPromises()

    expect(submitAnswer).toHaveBeenCalledWith(7, 'A안이되 롤백 절차 추가', ['o1'])
    wrapper.unmount()
  })

  it('replaces the pick rather than accumulating (v1 is single-select)', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()
    await openAnswerForm()

    optionButtons()[0].click()
    await flushPromises()
    optionButtons()[1].click()
    await flushPromises()

    expect(optionButtons().filter((b) => b.classList.contains('picked')).length).toBe(1)
    document.body.querySelector<HTMLButtonElement>('.qhd-answer-form .btn-primary')!.click()
    await flushPromises()
    expect(submitAnswer).toHaveBeenCalledWith(7, '', ['o2'])
    wrapper.unmount()
  })

  it('lets a user unpick, and blocks submitting an empty answer', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()
    await openAnswerForm()

    const submitBtn = document.body.querySelector<HTMLButtonElement>('.qhd-answer-form .btn-primary')!
    expect(submitBtn.disabled).toBe(true)   // nothing picked, nothing written

    optionButtons()[0].click()
    await flushPromises()
    expect(submitBtn.disabled).toBe(false)

    optionButtons()[0].click()              // same option again → unpick
    await flushPromises()
    expect(optionButtons()[0].classList.contains('picked')).toBe(false)
    expect(submitBtn.disabled).toBe(true)
    expect(submitAnswer).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('marks which option an answered query picked', async () => {
    const answered = optionItem({
      answer_count: 1,
      answers: [{ body: 'B안: 점검창 배포', author_kind: 'human', selected_options: ['o2'] }],
    })
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() }, answered)
    await flushPromises()

    const readOnly = Array.from(document.body.querySelectorAll('.qhd-opt-read'))
    expect(readOnly.length).toBe(2)
    expect(readOnly[0].classList.contains('picked')).toBe(false)
    expect(readOnly[1].classList.contains('picked')).toBe(true)
    wrapper.unmount()
  })

  it('shows no options UI for a query that carries none (unchanged surface)', async () => {
    const plain = optionItem({ options: [] })
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() }, plain)
    await flushPromises()
    await openAnswerForm()

    expect(optionButtons().length).toBe(0)
    expect(document.body.querySelector('.qhd-answer-textarea')).toBeTruthy()
    wrapper.unmount()
  })
})
