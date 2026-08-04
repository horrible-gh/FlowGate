import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

// R0001 (group 0086): the continuous-work dialog reads /workflow/sequence, marks the head
// (first non-done step) + disables earlier (done) steps so the run cannot skip or start in
// the middle, and confirms a target item_seq + review-mode flag for the warning gate.

const { getRequest, putRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest,
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
  putRequest.mockReset()
  putRequest.mockResolvedValue({ data: { ok: true } })
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

    const steps = document.querySelectorAll('.wsp-step')
    expect(steps).toHaveLength(6)
    // The two done (pre-head) steps are disabled — no skipping / no mid-start.
    expect((steps[0] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[1] as HTMLButtonElement).disabled).toBe(true)
    // 0337 R0001-1: the dialog opens in auto_approved mode, where the head (T, item_seq 3) is
    // written and approved by the SERVER — it is executed but is not a stop point, so it is
    // not offered as a choice either. TR (idx 3) onward stay selectable.
    expect((steps[2] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[3] as HTMLButtonElement).disabled).toBe(false)
    // Default target = last step → whole remaining sequence; [Next] is enabled.
    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    expect(next.disabled).toBe(false)
  })

  // 0337 R0001-1: "자동승인을 고르면 작업/프로바이더 둘다 N/T를 선택하도록 되어있는데 선택할
  // 이유가 없지 않나?" — under auto-approve the server writes and approves N/T with no AI
  // worker, so they must disappear from BOTH selections; ai_direct writes them with an AI, so
  // there they remain ordinary, selectable execution steps.
  it('excludes auto-approved N/T from the target choice and restores them in ai_direct', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    // auto_approved (default): the T step is read-only and labelled 자동 승인.
    const steps = document.querySelectorAll('.wsp-step')
    expect((steps[2] as HTMLButtonElement).disabled).toBe(true)
    expect(steps[2].querySelector('.wsp-step-tag--auto')?.textContent).toContain(
      i18n.global.t('main.continuous_work.auto_step_tag'),
    )
    // Clicking it cannot move the target.
    ;(steps[2] as HTMLButtonElement).click()
    await flushPromises()
    // 0388 NR0003: the sequence ends TS→TSR, so the default target is TS (steps[4]), one step
    // short of the paired report.
    expect(document.querySelectorAll('.wsp-step--target')[0]).toBe(steps[4])

    // Switch N/T handling to "AI 직접 작성" → T becomes a real AI step again.
    const aiRadio = document.querySelectorAll('.cwd-mode input')[1] as HTMLInputElement
    aiRadio.checked = true
    aiRadio.dispatchEvent(new Event('change'))
    await flushPromises()

    const afterSteps = document.querySelectorAll('.wsp-step')
    expect((afterSteps[2] as HTMLButtonElement).disabled).toBe(false)
    expect(afterSteps[2].querySelector('.wsp-step-tag--auto')).toBeNull()
    ;(afterSteps[2] as HTMLButtonElement).click()
    await flushPromises()

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()
    const payload = wrapper.emitted('confirm')![0][0] as any
    expect(payload.targetSeq).toBe(3)
    expect(payload.targetType).toBe('T')
  })

  // 0388 NR0003: a sequence ending in TS→TSR must default the target to TS (one step short of
  // the paired report), not TSR itself — the user still opened the dialog expecting to review
  // before the test report ships, not to fire it automatically. TSR stays selectable so the
  // user can still explicitly extend the target to it.
  it('defaults the target to TS instead of TSR when the sequence ends with the TS/TSR pair', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    const steps = document.querySelectorAll('.wsp-step')
    // steps[4] = TS (item_seq 5), steps[5] = TSR (item_seq 6, the paired report).
    expect(document.querySelectorAll('.wsp-step--target')[0]).toBe(steps[4])
    expect((steps[5] as HTMLButtonElement).disabled).toBe(false)

    ;(steps[5] as HTMLButtonElement).click()
    await flushPromises()
    expect(document.querySelectorAll('.wsp-step--target')[0]).toBe(steps[5])

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()
    const payload2 = wrapper.emitted('confirm')![0][0] as any
    expect(payload2.targetSeq).toBe(6)
    expect(payload2.targetType).toBe('TSR')

    wrapper.unmount()
  })

  // 0337 R0001-1: switching INTO auto-approve while an N/T step is the chosen target must not
  // leave a boundary the run can no longer honour — it re-points at the paired report that
  // ends the same logical unit (T@3 → TR@4).
  it('re-points an N/T target at its paired report when auto-approve is switched back on', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    const aiRadio = document.querySelectorAll('.cwd-mode input')[1] as HTMLInputElement
    aiRadio.checked = true
    aiRadio.dispatchEvent(new Event('change'))
    await flushPromises()
    ;(document.querySelectorAll('.wsp-step')[2] as HTMLButtonElement).click()
    await flushPromises()

    const autoRadio = document.querySelectorAll('.cwd-mode input')[0] as HTMLInputElement
    autoRadio.checked = true
    autoRadio.dispatchEvent(new Event('change'))
    await flushPromises()

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()
    const payload = wrapper.emitted('confirm')![0][0] as any
    expect(payload.targetSeq).toBe(4)
    expect(payload.targetType).toBe('TR')
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

    const container = document.querySelector('.wsp-steps') as HTMLElement
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
        return this.classList.contains('wsp-steps') ? 360 : 0
      })

    const wrapper = mountDialog()
    await flushPromises()
    await nextTick()

    // No bare error state — the step list is rendered so the user can see what ran.
    expect(document.querySelector('.wsp-state--error')).toBeNull()
    const steps = document.querySelectorAll('.wsp-step')
    expect(steps).toHaveLength(4)
    // All done (head=null) → the list is scrolled to the very bottom (scrollHeight) so the most
    // recent step shows instead of leaving the user parked on the completed steps at the top.
    expect((document.querySelector('.wsp-steps') as HTMLElement).scrollTop).toBe(360)
    // All four are shown as done (read-only) and the all-done note is visible.
    expect(document.querySelectorAll('.wsp-step--done')).toHaveLength(4)
    expect(document.querySelector('.wsp-note--info')?.textContent).toContain(
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
    const steps = document.querySelectorAll('.wsp-step')
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
    expect(payload.targetType).toBe('TR')
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

    expect(document.querySelector('.wsp-state--error')).toBeNull()
    expect(document.querySelector('.wsp-note--info')?.textContent).toContain(
      i18n.global.t('main.continuous_work.from_decision_note'),
    )
    const steps = document.querySelectorAll('.wsp-step')
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
    expect(payload.targetType).toBe('')
  })

  it('starts from the workflow decision on a 400 sequence_not_decided fallback (R0001 워크플로 결정부터)', async () => {
    getRequest.mockRejectedValue({ response: { status: 400, data: { error: 'sequence_not_decided' } } })
    const wrapper = mountDialog()
    await flushPromises()

    // Not blocked: the dialog offers to start FROM the workflow-decision step. The
    // pre-decision note is shown and there is exactly one (static) head step.
    expect(document.querySelector('.wsp-state--error')).toBeNull()
    expect(document.querySelector('.wsp-note--info')?.textContent).toContain(
      i18n.global.t('main.continuous_work.from_decision_note'),
    )
    const steps = document.querySelectorAll('.wsp-step')
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
    expect(payload.targetType).toBe('')
  })

  // 0317 T0010 rev4: the doc-type-keyed assignment table (D0004) was replaced by a per-STEP
  // (item_seq) override table under a new "프로바이더" tab — the same doc TYPE appearing twice
  // in a chain (two T steps) can now resolve to different providers. Session-scoped: it rides
  // the confirm payload / start request only, never a persisted PUT.
  // 0317 T0015: the per-step rows are shown directly (no "단계별로 다르게 지정" disclosure) and
  // each select is pre-selected to the header default provider (no blank "use default" option).
  it('overrides the provider for one specific step and reports it in the confirm payload', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mount(ContinuousWorkDialog, {
      props: {
        visible: true,
        docRef: 'flowgate.default.0086.0001-R',
        providers: [
          { id: 'aip_fable', name: 'Fable' },
          { id: 'aip_opus', name: 'Opus' },
        ],
        selectedProvider: 'aip_fable',
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    // "기본 설정" is the default active tab (rev4 STATE1) — switch to "프로바이더".
    // flowgate.default.0346 T0005 added a third [전달멘트] tab alongside these two.
    const tabs = document.querySelectorAll('.cwd-tab')
    expect(tabs).toHaveLength(3)
    ;(tabs[1] as HTMLButtonElement).click()
    await flushPromises()

    // 0317 T0015: no per-step disclosure toggle — the step rows are shown directly.
    expect(document.querySelector('.cwd-disclosure-btn')).toBeNull()
    // 0337 R0001-1: one row per step an AI worker actually runs. The 2 done steps (N, NR) and
    // the auto-approved T are excluded. 0388 NR0003: the default target also stops at TS (one
    // step short of the paired TSR report) → TR, TS = 2.
    const rows = document.querySelectorAll('.cwd-override-row')
    expect(rows).toHaveLength(2)
    const selects = document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
    // 0317 T0015: each step is pre-selected to the header default provider (not a blank option).
    expect(selects[0].value).toBe('aip_fable')
    expect(selects[1].value).toBe('aip_fable')
    // Override the first AI-execution step (TR, item_seq 4) to Opus.
    selects[0].value = 'aip_opus'
    selects[0].dispatchEvent(new Event('change'))
    await flushPromises()

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()

    const ev = wrapper.emitted('confirm')
    expect(ev).toHaveLength(1)
    const payload = ev![0][0] as any
    // Only the genuinely-overridden step is reported; steps left at the default are not.
    expect(payload.providerOverrides).toEqual({ 4: 'aip_opus' })
    // No project-level persistence — the old D0004 PUT never fires for a per-step override.
    expect(putRequest).not.toHaveBeenCalled()
  })

  // 0337 R0001-2: "작업을 6단계 중 5단계만 선택하면 마찬가지로 6단계의 프로바이더를 선택할
  // 이유가 없잖아" — the provider table is the run, not the sequence. Shrinking the target must
  // remove the rows beyond it, and an override already made for a removed row must not survive
  // into the payload.
  it('drops provider rows past the chosen stop point and forgets their overrides', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mount(ContinuousWorkDialog, {
      props: {
        visible: true,
        docRef: 'flowgate.default.0086.0001-R',
        providers: [
          { id: 'aip_fable', name: 'Fable' },
          { id: 'aip_opus', name: 'Opus' },
        ],
        selectedProvider: 'aip_fable',
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
    await flushPromises()

    // 0388 NR0003: the default target now stops at TS (item_seq 5) → TR, TS = 2 rows.
    let selects = document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
    expect(selects).toHaveLength(2)

    // Extend the target to TSR (item_seq 6) — still selectable, just no longer the default.
    ;(document.querySelectorAll('.wsp-step')[5] as HTMLButtonElement).click()
    await flushPromises()
    selects = document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
    expect(selects).toHaveLength(3)
    // Override the LAST step (TSR, item_seq 6) — then take it out of the run again.
    selects[2].value = 'aip_opus'
    selects[2].dispatchEvent(new Event('change'))
    await flushPromises()

    // Stop at step 5 of 6 (TS, item_seq 5) again: the 6th step's provider row disappears.
    ;(document.querySelectorAll('.wsp-step')[4] as HTMLButtonElement).click()
    await flushPromises()
    selects = document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
    expect(selects).toHaveLength(2)
    // ...and the step-6 provider tag is gone from the step list too.
    expect(document.querySelectorAll('.wsp-prov-tag')).toHaveLength(2)

    const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
    next.click()
    await flushPromises()

    const payload = wrapper.emitted('confirm')![0][0] as any
    expect(payload.targetSeq).toBe(5)
    // The stale item_seq 6 override is NOT carried into the run.
    expect(payload.providerOverrides).toEqual({})

    wrapper.unmount()
  })

  // 0317 T0010 rev4 STATE 4: the "프로바이더" tab is replaced by a guidance card (not disabled
  // controls) when the project has zero registered providers; "기본 설정" stays usable either way.
  it('shows an empty-provider guidance card when no providers are registered', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mount(ContinuousWorkDialog, {
      props: { visible: true, docRef: 'flowgate.default.0086.0001-R', providers: [] },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
    await flushPromises()

    expect(document.querySelector('.cwd-empty-card')).not.toBeNull()
    expect(document.querySelector('.cwd-provider-block')).toBeNull()

    wrapper.unmount()
  })

  // flowgate.default.0346 T0005 / D0004 §3-1~§3-3: the [전달멘트] tab collects a common note
  // (whole chain) and per-step notes (individual hops), both carried — unmodified — into the
  // confirm payload. Neither is persisted; both ride this run's start request only.
  describe('전달멘트 tab (0346 T0005)', () => {
    it('fills the common and per-step notes and reports them in the confirm payload', async () => {
      getRequest.mockResolvedValue(seqResponse())
      const wrapper = mountDialog()
      await flushPromises()

      const tabs = document.querySelectorAll('.cwd-tab')
      ;(tabs[2] as HTMLButtonElement).click()
      await flushPromises()

      const defaultInput = document.querySelector('.cwd-message-default-input') as HTMLInputElement
      defaultInput.value = '이 그룹은 결제 모듈 리팩터링입니다'
      defaultInput.dispatchEvent(new Event('input'))
      await flushPromises()

      // 3 rows: TR, TS, TSR (same run scope as the provider tab's table).
      const rowInputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
      expect(rowInputs).toHaveLength(3)
      rowInputs[0].value = 'TR: 결제 실패 케이스도 문서화해줘'
      rowInputs[0].dispatchEvent(new Event('input'))
      await flushPromises()

      const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
      next.click()
      await flushPromises()

      const payload = wrapper.emitted('confirm')![0][0] as any
      expect(payload.defaultMessage).toBe('이 그룹은 결제 모듈 리팩터링입니다')
      expect(payload.messageOverrides).toEqual({ 4: 'TR: 결제 실패 케이스도 문서화해줘' })

      wrapper.unmount()
    })

    it('treats a whitespace-only per-step note as no override', async () => {
      getRequest.mockResolvedValue(seqResponse())
      const wrapper = mountDialog()
      await flushPromises()

      ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
      await flushPromises()

      const rowInputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
      rowInputs[0].value = '   '
      rowInputs[0].dispatchEvent(new Event('input'))
      await flushPromises()

      const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
      next.click()
      await flushPromises()

      const payload = wrapper.emitted('confirm')![0][0] as any
      expect(payload.defaultMessage).toBe('')
      expect(payload.messageOverrides).toEqual({})

      wrapper.unmount()
    })

    it('drops a per-step note when its row leaves the run and resets both fields on reopen', async () => {
      getRequest.mockResolvedValue(seqResponse())
      const wrapper = mountDialog()
      await flushPromises()

      ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
      await flushPromises()

      // Note the LAST row (TSR, item_seq 6), then shrink the target past it.
      let rowInputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
      rowInputs[2].value = 'TSR용 멘트'
      rowInputs[2].dispatchEvent(new Event('input'))
      await flushPromises()

      ;(document.querySelectorAll('.wsp-step')[4] as HTMLButtonElement).click()
      await flushPromises()
      rowInputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
      expect(rowInputs).toHaveLength(2)

      const next = [...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement
      next.click()
      await flushPromises()
      expect((wrapper.emitted('confirm')![0][0] as any).messageOverrides).toEqual({})

      // Reopening the dialog must not carry the note forward (D0004 §3-1: 닫았다 다시 열면 초기화).
      await wrapper.setProps({ visible: false })
      await flushPromises()
      await wrapper.setProps({ visible: true })
      await flushPromises()
      ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
      await flushPromises()
      expect((document.querySelector('.cwd-message-default-input') as HTMLInputElement).value).toBe('')
      const freshRowInputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
      freshRowInputs.forEach((el) => expect(el.value).toBe(''))

      wrapper.unmount()
    })
  })
})
