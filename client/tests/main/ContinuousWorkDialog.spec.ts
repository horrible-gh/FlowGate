import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

// R0001 (group 0086): the continuous-work dialog reads /workflow/sequence, marks the head
// (first non-done step) + disables earlier (done) steps so the run cannot skip or start in
// the middle, and confirms a target item_seq + review-mode flag for the warning gate.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

// The LIVE /workflow/sequence is served by workflow_head_routes, whose shape is
// { decided, sequence, head } — NOT the { items } shape of the shadowed decision_routes
// handler. The dialog must read this shape (regression: it previously read only `items`,
// which is never present live, so every open showed "워크플로 단계가 없습니다").
// N done, NR done, T (head=pending), TR, TS, TSR — head is the 3rd item (item_seq 3).
function seqResponse() {
  return {
    data: {
      doc_id: 'flowgate.default.0086.0001-R',
      doc_class: 'R',
      decided: true,
      sequence: [
        { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
        { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
        { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
        { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 5, item_seq: 5, type: 'TS', label: '테스트시나리오', status: 'pending' },
        { id: 6, item_seq: 6, type: 'TSR', label: '테스트레포트', status: 'pending' },
      ],
      head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

function mountDialog() {
  return mount(ContinuousWorkDialog, {
    props: { visible: true, docRef: 'flowgate.default.0086.0001-R' },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  getRequest.mockReset()
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('ContinuousWorkDialog', () => {
  it('disables done steps and defaults the target to the last step', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    const steps = document.querySelectorAll('.cwd-step')
    expect(steps).toHaveLength(6)
    // The two done (pre-head) steps are disabled — no skipping / no mid-start.
    expect((steps[0] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[1] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[2] as HTMLButtonElement).disabled).toBe(false)
    // Default target = last step → whole remaining sequence; [Next] is enabled.
    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    expect(next.disabled).toBe(false)
  })

  it('confirms the chosen target item_seq, step count, and review mode', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    // Pick the 4th item (TR, item_seq 4) as the stop point.
    const steps = document.querySelectorAll('.cwd-step')
    ;(steps[3] as HTMLButtonElement).click()
    // Enable AI review mode.
    const toggle = document.querySelector('.cwd-toggle input') as HTMLInputElement
    toggle.checked = true
    toggle.dispatchEvent(new Event('change'))
    await flushPromises()

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()

    const ev = wrapper.emitted('confirm')
    expect(ev).toHaveLength(1)
    const payload = ev![0][0] as any
    expect(payload.targetSeq).toBe(4)
    // head=item3 .. target=item4 inclusive → 2 steps.
    expect(payload.stepCount).toBe(2)
    expect(payload.reviewMode).toBe(true)
  })

  it('starts from the workflow decision when the live endpoint returns 200 decided:false (R0001 워크플로 결정부터)', async () => {
    // head_routes returns 200 + decided:false (NOT a 400) for an undecided sequence. The
    // dialog must NOT fall into error_empty ("워크플로 단계가 없습니다") here.
    getRequest.mockResolvedValue({ data: { doc_id: 'flowgate.default.0086.0001-R', doc_class: 'R', decided: false, sequence: [], head: null } })
    const wrapper = mountDialog()
    await flushPromises()

    expect(document.querySelector('.cwd-state--error')).toBeNull()
    expect(document.querySelector('.cwd-note--info')?.textContent).toContain(
      i18n.global.t('main.continuous_work.from_decision_note'),
    )
    const steps = document.querySelectorAll('.cwd-step')
    expect(steps).toHaveLength(1)

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    expect(next.disabled).toBe(false)
    next.click()
    await flushPromises()

    const ev = wrapper.emitted('confirm')
    expect(ev).toHaveLength(1)
    const payload = ev![0][0] as any
    expect(payload.fromDecision).toBe(true)
    expect(payload.targetSeq).toBe(-1)
  })

  it('starts from the workflow decision on a 400 sequence_not_decided fallback (R0001 워크플로 결정부터)', async () => {
    getRequest.mockRejectedValue({ response: { status: 400, data: { error: 'sequence_not_decided' } } })
    const wrapper = mountDialog()
    await flushPromises()

    // Not blocked: the dialog offers to start FROM the workflow-decision step. The
    // pre-decision note is shown and there is exactly one (static) head step.
    expect(document.querySelector('.cwd-state--error')).toBeNull()
    expect(document.querySelector('.cwd-note--info')?.textContent).toContain(
      i18n.global.t('main.continuous_work.from_decision_note'),
    )
    const steps = document.querySelectorAll('.cwd-step')
    expect(steps).toHaveLength(1)

    // [Next] is enabled and confirm emits the run-to-end sentinel + fromDecision flag.
    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    expect(next.disabled).toBe(false)
    next.click()
    await flushPromises()

    const ev = wrapper.emitted('confirm')
    expect(ev).toHaveLength(1)
    const payload = ev![0][0] as any
    expect(payload.fromDecision).toBe(true)
    expect(payload.targetSeq).toBe(-1)
  })
})
