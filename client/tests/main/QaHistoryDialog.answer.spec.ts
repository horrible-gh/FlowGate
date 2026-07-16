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

  it('exposes [답변 작성] / [멘트 복사] / [AI에게 답변 요청] when a docId is supplied', async () => {
    mountDialog({ docId: 'test.test.0093.0001-R', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() })
    await flushPromises()
    expect(document.body.querySelector('.qhd-act-write')).toBeTruthy()
    expect(document.body.querySelector('.qhd-act-mention')).toBeTruthy()
    expect(document.body.querySelector('.qhd-act-ai')).toBeTruthy()
  })

  it('calls submitAnswer with (itemId, body, selectedOptionIds) and closes the form on success', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer, requestAiAnswer: vi.fn() })
    await flushPromises()

    // open the answer form
    const writeBtn = document.body.querySelector<HTMLButtonElement>('.qhd-act-write')!
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

    // no option picked (this query carries none) → empty selection (group 0243 R0001)
    expect(submitAnswer).toHaveBeenCalledWith(7, '내 답변', [])
    // form closes after a successful submit
    expect(document.body.querySelector('.qhd-answer-form')).toBeNull()
    wrapper.unmount()
  })

  it('calls requestAiAnswer with the item id', async () => {
    const requestAiAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer })
    await flushPromises()

    const aiBtn = document.body.querySelector<HTMLButtonElement>('.qhd-act-ai')!
    aiBtn.click()
    await flushPromises()

    expect(requestAiAnswer).toHaveBeenCalledWith(7, undefined)
    wrapper.unmount()
  })


  it('places the provider selector on the left and groups action buttons on the right', async () => {
    const wrapper = mountDialog({ docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn() })
    await flushPromises()

    const handoff = document.body.querySelector<HTMLElement>('.qhd-handoff')!
    expect(handoff.firstElementChild?.classList.contains('qhd-provider-select')).toBe(true)
    expect(handoff.lastElementChild?.classList.contains('qhd-handoff-actions')).toBe(true)
    expect(handoff.querySelectorAll('.qhd-handoff-actions > button')).toHaveLength(2)
    wrapper.unmount()
  })


  it('shows the provider selector and forwards the selected provider to the AI request', async () => {
    const requestAiAnswer = vi.fn().mockResolvedValue(true)
    const selectProvider = vi.fn()
    const wrapper = mountDialog({
      docId: 'd1',
      submitAnswer: vi.fn(),
      requestAiAnswer,
      aiProviders: [{ id: 'provider-a', name: 'Provider A' }, { id: 'provider-b', name: 'Provider B' }],
      selectedProviderId: 'provider-b',
      selectProvider,
    })
    await flushPromises()

    const select = document.body.querySelector<HTMLSelectElement>('.qhd-provider-select select')!
    expect(select).toBeTruthy()
    expect(select.value).toBe('provider-b')
    document.body.querySelector<HTMLButtonElement>('.qhd-act-ai')!.click()
    await flushPromises()

    expect(requestAiAnswer).toHaveBeenCalledWith(7, 'provider-b')
    wrapper.unmount()
  })

  // group 0248 B0001: the AI answer arrives from an async server run, so the button must
  // show that the run is live and refuse a second click. Before the fix there was no run
  // to be in — the click was a no-op — so nothing conveyed "working on it".
  it('marks the item as in progress and blocks re-clicks while its AI run is live', async () => {
    const requestAiAnswer = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({
      docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer, aiRunItemId: 7,
    })
    await flushPromises()

    const aiBtn = document.body.querySelector<HTMLButtonElement>('.qhd-act-ai')!
    expect(aiBtn.disabled).toBe(true)
    // Compare against the active locale's string — this suite does not pin a locale.
    expect(aiBtn.textContent).toContain(i18n.global.t('main.doc_info_panel.qa_answer_ai_running'))

    aiBtn.click()
    await flushPromises()
    expect(requestAiAnswer).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('leaves the button idle when no AI run is in flight', async () => {
    const wrapper = mountDialog({
      docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn(), aiRunItemId: null,
    })
    await flushPromises()

    const aiBtn = document.body.querySelector<HTMLButtonElement>('.qhd-act-ai')!
    expect(aiBtn.disabled).toBe(false)
    expect(aiBtn.textContent).toContain(i18n.global.t('main.doc_info_panel.qa_answer_ai'))
    wrapper.unmount()
  })
})

// 0248 B0001 rework — the rejected behaviour, pinned.
//
// Reviewer: "질문을 등록했는데 AI가 답변이 불가능하다. 멘트복사도 없고 AI호출도 없고
// 사용자가 질문하고 사용자가 답하는 자문자답?"
//
// The panel card's [답변] opens this dialog with startAnswer=true, which opens the compose
// form. The hand-off buttons used to live in that form's v-else, so arriving from the card —
// the ONLY route a user takes after registering a query — rendered a bare textarea and no way
// to reach an AI at all. Writing an answer and handing it off are not alternatives.
describe('QaHistoryDialog AI hand-off reachability (0248 B0001 rework)', () => {
  it('keeps [멘트 복사] and [AI에게 답변 요청] reachable while the compose form is open', async () => {
    // Drive it exactly as the panel card does: the dialog is mounted closed and opened with
    // a focus target + startAnswer. (The form-opening watcher fires on the visible
    // transition, so mounting with visible:true would never open the box.)
    const wrapper = mountDialog({
      visible: false,
      docId: 'd1',
      submitAnswer: vi.fn(),
      requestAiAnswer: vi.fn(),
      copyAnswerMention: vi.fn(),
      focusId: 7,
      startAnswer: true,
    })
    await wrapper.setProps({ visible: true })
    await flushPromises()

    // The compose box is open — this is the state the reviewer was stuck in.
    expect(document.body.querySelector('.qhd-answer-textarea')).toBeTruthy()
    // ...and the hand-off must still be right there.
    expect(document.body.querySelector('.qhd-act-mention')).toBeTruthy()
    expect(document.body.querySelector('.qhd-act-ai')).toBeTruthy()
    wrapper.unmount()
  })

  it('calls copyAnswerMention with the item id', async () => {
    const copyAnswerMention = vi.fn().mockResolvedValue(true)
    const wrapper = mountDialog({
      docId: 'd1', submitAnswer: vi.fn(), requestAiAnswer: vi.fn(), copyAnswerMention,
    })
    await flushPromises()

    document.body.querySelector<HTMLButtonElement>('.qhd-act-mention')!.click()
    await flushPromises()

    expect(copyAnswerMention).toHaveBeenCalledWith(7)
    wrapper.unmount()
  })

  it('offers the hand-off even to an item that already has an answer (re-request)', async () => {
    const wrapper = mountDialog({
      docId: 'd1',
      items: [{ ...items()[0], answer_count: 1, answers: [{ body: '사람 답변', author_kind: 'human' }] }],
      submitAnswer: vi.fn(),
      requestAiAnswer: vi.fn(),
      copyAnswerMention: vi.fn(),
    })
    await flushPromises()

    expect(document.body.querySelector('.qhd-act-mention')).toBeTruthy()
    expect(document.body.querySelector('.qhd-act-ai')).toBeTruthy()
    wrapper.unmount()
  })
})