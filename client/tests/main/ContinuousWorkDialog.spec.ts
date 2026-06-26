import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
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

  it('scrolls the running (head) step into view so completed steps do not hide it (R0001 0129)', async () => {
    getRequest.mockResolvedValue(seqResponse())

    // jsdom does no layout, so fake the layout offset revealActiveStep() reads. The reveal now
    // uses offsetTop (untransformed layout coordinate, immune to the modal open `scale()`
    // animation) instead of getBoundingClientRect. Each step row is 40px tall stacked from the
    // container top → head item_seq 3 → idx 2 → offsetTop 80.
    const offsetSpy = vi
      .spyOn(HTMLElement.prototype, 'offsetTop', 'get')
      .mockImplementation(function (this: HTMLElement) {
        const idx = this.getAttribute?.('data-step-idx')
        return idx != null ? Number(idx) * 40 : 0
      })

    const wrapper = mountDialog()
    await flushPromises()
    await nextTick()

    const container = document.querySelector('.cwd-steps') as HTMLElement
    // The two done steps (idx 0,1) scroll off the top; the head row (idx 2, offsetTop 80) is
    // aligned to the container top → scrollTop is 80 instead of staying parked at 0.
    expect(container.scrollTop).toBe(80)

    offsetSpy.mockRestore()
    wrapper.unmount()
  })

  it('shows the executed step list (not a bare error) when every step is done (R0001 0129 repro 0094)', async () => {
    // Live repro flowgate.default.0094.0001-R: 8 T/TR steps, ALL done, head=null. The dialog
    // used to set errorKey='error_all_done', replacing the whole body with a one-line error
    // banner so the user "들어가보면 전부 완료된것만 보이잖아" — the executed steps were hidden.
    getRequest.mockResolvedValue({
      data: {
        doc_id: 'flowgate.default.0094.0001-R',
        doc_class: 'R',
        decided: true,
        sequence: [
          { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'done' },
          { id: 2, item_seq: 2, type: 'TR', label: '작업레포트', status: 'done' },
          { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'done' },
          { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'done' },
        ],
        head: null,
      },
    })
    // Fake the capped list overflowing (jsdom has no layout): scrollHeight 360 > clientHeight 280.
    const scrollHeightSpy = vi
      .spyOn(HTMLElement.prototype, 'scrollHeight', 'get')
      .mockImplementation(function (this: HTMLElement) {
        return this.classList.contains('cwd-steps') ? 360 : 0
      })

    const wrapper = mountDialog()
    await flushPromises()
    await nextTick()

    // No bare error state — the step list is rendered so the user can see what ran.
    expect(document.querySelector('.cwd-state--error')).toBeNull()
    const steps = document.querySelectorAll('.cwd-step')
    expect(steps).toHaveLength(4)
    // All done (head=null) → the list is scrolled to the very bottom (scrollHeight) so the most
    // recent step shows instead of leaving the user parked on the completed steps at the top.
    expect((document.querySelector('.cwd-steps') as HTMLElement).scrollTop).toBe(360)
    // All four are shown as done (read-only) and the all-done note is visible.
    expect(document.querySelectorAll('.cwd-step--done')).toHaveLength(4)
    expect(document.querySelector('.cwd-note--info')?.textContent).toContain(
      i18n.global.t('main.continuous_work.all_done_note'),
    )
    // Nothing left to continue → [Next] disabled, review-mode toggle hidden.
    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    expect(next.disabled).toBe(true)
    expect(document.querySelector('.cwd-toggle')).toBeNull()

    scrollHeightSpy.mockRestore()
    wrapper.unmount()
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
