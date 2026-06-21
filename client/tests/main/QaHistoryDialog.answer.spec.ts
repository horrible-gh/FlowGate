import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import QaHistoryDialog from '@main/components/QaHistoryDialog.vue'

// group 0093 R0001 / T0004: the "질의 응답 전체 보기" dialog must be answer-capable —
// not just read-only. When given a docId and the shared write actions, each item
// exposes [답변 작성] / [AI에게 답변 요청]; submitting calls the bound action with
// (itemId, body). Without a docId it stays read-only (back-compat).

function items() {
  return [
    {
      id: 7,
      seq: 1,
      title: '질문 제목',
      body: '질문 본문',
      asker_kind: 'human',
      answer_count: 0,
      answers: [],
    },
  ]
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(QaHistoryDialog, {
    props: { visible: true, items: items(), ...props },
    global: { plugins: [i18n] },
  })
}

afterEach(() => {
  // The dialog teleports to <body>; clear any leaked modal between tests.
  document.body.querySelectorAll('.modal-qhd').forEach((n) => n.closest('.modal-bg')?.remove())
})

describe('QaHistoryDialog answer capability (group 0093 R0001)', () => {
  it('stays read-only when no docId is supplied (no answer actions)', async () => {
    mountDialog()
    await flushPromises()
    expect(document.body.querySelector('.qhd-answer-actions')).toBeNull()
    expect(document.body.querySelector('.qhd-answer-form')).toBeNull()
  })

  it('exposes [답변 작성] / [AI에게 답변 요청] when a docId is supplied', async () => {
    mountDialog({ docId: 'test.test.0093.0001-R', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() })
    await flushPromises()
    const buttons = Array.from(document.body.querySelectorAll('.qhd-answer-actions .btn'))
    expect(buttons.length).toBe(2)
  })

  it('calls submitAnswer with (itemId, body) and closes the form on success', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()

    // open the answer form — [Write answer] is the first action button
    const writeBtn = document.body.querySelectorAll<HTMLButtonElement>('.qhd-answer-actions .btn')[0]
    writeBtn.click()
    await flushPromises()

    const textarea = document.body.querySelector<HTMLTextAreaElement>('.qhd-answer-textarea')!
    expect(textarea).toBeTruthy()
    textarea.value = '내 답변'
    textarea.dispatchEvent(new Event('input'))
    await flushPromises()

    // [Submit answer] is the primary button in the form
    const submitBtn = document.body.querySelector<HTMLButtonElement>('.qhd-answer-form .btn-primary')!
    submitBtn.click()
    await flushPromises()

    expect(submitAnswer).toHaveBeenCalledWith(7, '내 답변')
    // form closes after a successful submit
    expect(document.body.querySelector('.qhd-answer-form')).toBeNull()
    wrapper.unmount()
  })

  it('calls requestAiAnswer with the item id', async () => {
    const requestAiAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer })
    await flushPromises()

    // [Ask AI to answer] is the second action button
    const aiBtn = document.body.querySelectorAll<HTMLButtonElement>('.qhd-answer-actions .btn')[1]
    aiBtn.click()
    await flushPromises()

    expect(requestAiAnswer).toHaveBeenCalledWith(7)
    wrapper.unmount()
  })
})
