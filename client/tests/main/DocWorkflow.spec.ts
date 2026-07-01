import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import type { StepState } from '@main/workflow/workflowViewState'

function ss(code: string, visual: 'done' | 'highlight' | 'rejected' | 'current' | 'future'): StepState {
  const className = {
    done: 'done',
    highlight: 'wf-next-action dip-step-clickable dip-step-active',
    rejected: 'wf-rejected dip-step-rejected',
    current: 'current',
    future: 'future dip-step-disabled',
  }[visual]
  const iconClass = {
    done: 'fa-solid fa-circle-check',
    highlight: 'fa-regular fa-circle',
    rejected: 'fa-solid fa-circle-xmark',
    current: 'fa-regular fa-circle-dot',
    future: 'fa-regular fa-circle',
  }[visual]
  return { code, visual, className, iconClass }
}
beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

describe('DocWorkflow — stepStates props (T839 D031 v2)', () => {
  function mountComp(overrides: Record<string, unknown> = {}) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.doc', typeCode: 'R' },
        workflowDecided: true,
        stepStates: [] as StepState[],
        ...overrides,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  // ── Regression 1: R decided + head=DS → R done, DS wf-next-action ──

  it('R1: R decided + head=DS → R done, DS wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
        ss('T', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[0].classes()).toContain('done')            // R — done
    expect(steps[1].classes()).toContain('wf-next-action')  // DS — highlighted
    expect(steps[0].classes()).not.toContain('wf-next-action')
    expect(steps[2].classes()).not.toContain('wf-next-action') // D
    expect(steps[3].classes()).not.toContain('wf-next-action') // T
  })

  // ── Regression 2: R decided + head in_progress → D highlighted (canNextAction=false) ──

  it('R2: R decided + head in_progress → D highlighted (canNextAction=false, not clickable)', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('D', 'highlight'),
        ss('T', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-next-action')          // D — highlighted
    expect(steps[1].classes()).not.toContain('wf-current-clickable') // not clickable
  })

  // ── Regression 3: R undecided → undecided placeholder ──

  it('R3: R undecided → undecided placeholder step visible', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: false,
      stepStates: [],
    })
    const undecided = wrapper.find('.wf-undecided')
    expect(undecided.exists()).toBe(true)
    const steps = wrapper.findAll('.wf-step')
    steps.forEach((step) => {
      expect(step.classes()).not.toContain('wf-next-action')
    })
  })

  // ── Regression 4: non-R approved + next step → next step wf-next-action ──

  it('R4: non-R DS approved + T highlighted → T wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'done'),
        ss('T', 'highlight'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[2].classes()).toContain('wf-next-action')   // T
    expect(steps[0].classes()).not.toContain('wf-next-action') // R
    expect(steps[1].classes()).not.toContain('wf-next-action') // DS
    expect(steps[3].classes()).not.toContain('wf-next-action') // AC
  })

  // ── Regression 5: single-activation only — only the head step is highlighted ──

  it('R5: only head step gets wf-next-action (single activation per step)', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'done'),
        ss('D', 'highlight'),
        ss('P', 'future'),
        ss('L', 'future'),
        ss('DB', 'future'),
        ss('V', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[2].classes()).toContain('wf-next-action') // D — only D highlighted
    expect(steps[3].classes()).not.toContain('wf-next-action') // P — not activated
    expect(steps[4].classes()).not.toContain('wf-next-action') // L
    expect(steps[5].classes()).not.toContain('wf-next-action') // DB
    expect(steps[6].classes()).not.toContain('wf-next-action') // V
    expect(steps[7].classes()).not.toContain('wf-next-action') // AC
  })

  // ── Regression 6: wf_done → no highlight ──

  it('R6: wf_done (all done) → no wf-next-action on any step', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('D', 'done'),
        ss('T', 'done'),
        ss('AC', 'done'),
      ],
      canNextAction: false,
    })
    wrapper.findAll('.wf-step').forEach((step) => {
      expect(step.classes()).not.toContain('wf-next-action')
    })
  })

  // ── Regression 7: click on highlight step when canNextAction=true → emit 'next-action' ──

  it('R7: click on highlight step when canNextAction=true → emits "next-action"', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-current-clickable') // DS is clickable
    await steps[1].trigger('click')
    expect(wrapper.emitted('next-action')).toBeTruthy()
    expect(wrapper.emitted('next-action')!.length).toBe(1)
  })

  // ── New regression cases (T839) ──

  it('NR1: rejected step → wf-rejected CSS class applied', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('M', 'done'),
        ss('DS', 'rejected'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[2].classes()).toContain('wf-rejected')     // DS — rejected
    expect(steps[2].classes()).not.toContain('wf-next-action')
    expect(steps[0].classes()).not.toContain('wf-rejected')  // R — done
  })

  it('NR2: R decided + DS pending (canNextAction=true) → DS clickable wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-next-action')
    expect(steps[1].classes()).toContain('wf-current-clickable')
  })

  it('NR3: R decided + DS in_progress (canNextAction=false) → DS wf-next-action but not clickable', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-next-action')
    expect(steps[1].classes()).not.toContain('wf-current-clickable')
  })

  it('NR4: R undecided (stepStates=[], workflowDecided=false) → shows undecided placeholder', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: false,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.findAll('.wf-undecided').length).toBe(2)
    expect(wrapper.findAll('.wf-step').length).toBe(2)
  })

  it('NR5: R wf_done (all done) → all steps show "done" class, no wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'done'),
        ss('D', 'done'),
        ss('T', 'done'),
        ss('AC', 'done'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    steps.forEach((step) => {
      expect(step.classes()).toContain('done')
      expect(step.classes()).not.toContain('wf-next-action')
    })
  })

  it('NR6: non-R DS tab in pending_review → DS shows wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-next-action')
    expect(steps[0].classes()).toContain('done')
  })

  it('NR7: non-R DS rejected → DS shows wf-rejected', () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'rejected'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[1].classes()).toContain('wf-rejected')
    expect(steps[1].classes()).not.toContain('wf-next-action')
  })

  it('NR8: click on non-highlight step → does NOT emit next-action', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'highlight'),
        ss('D', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    await steps[0].trigger('click') // R (done) — not highlight
    await steps[2].trigger('click') // D (future) — not highlight
    expect(wrapper.emitted('next-action')).toBeFalsy()
  })

  it('NR9: wf-section hidden when stepStates=[] and not R-undecided', () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(false)
  })
})

// ── T869: render current-step highlighting for an undecided M tab (DocWorkflow.vue) ──
//
// T869-S4: verify that an M head step renders wf-next-action and wf-current-clickable on the M cell.
// When workflowViewState emits M as 'highlight', DocWorkflow.vue applies both classes
//   :class="[s.className, s.visual === 'highlight' && canNextAction ? 'wf-current-clickable' : '']"
// through className=wf-next-action and canNextAction=true.
// The wf-undecided placeholders are exclusive to R tabs and must not appear on M tabs.

describe('DocWorkflow — M-tab 미결정 현재 단계 강조 렌더링 (T869)', () => {
  function mountComp(overrides: Record<string, unknown> = {}) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.m', typeCode: 'M' },
        workflowDecided: true,
        stepStates: [] as StepState[],
        ...overrides,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  // T869-S4: M-tab head is pending and canNextAction=true.
  // Apply wf-next-action and wf-current-clickable together; clicking emits next-action.
  // Do not show the R-tab undecided placeholders (.wf-undecided).
  it('T869-S4: M 탭, M이 헤드(canNextAction=true) → M 스텝 wf-next-action + wf-current-clickable, wf-undecided 미표시', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.m', typeCode: 'M' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('M', 'highlight'),
        ss('DS', 'future'),
        ss('D', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    // R is done and has no highlight.
    expect(steps[0].classes()).toContain('done')
    expect(steps[0].classes()).not.toContain('wf-next-action')
    // M: highlight + clickable
    expect(steps[1].classes()).toContain('wf-next-action')
    expect(steps[1].classes()).toContain('wf-current-clickable')
    // DS and D are future steps with no highlight.
    expect(steps[2].classes()).not.toContain('wf-next-action')
    expect(steps[3].classes()).not.toContain('wf-next-action')
    // No wf-undecided placeholders; M tabs do not use the R-only undecided UI.
    expect(wrapper.find('.wf-undecided').exists()).toBe(false)
    // Clicking emits the next-action event.
    await steps[1].trigger('click')
    expect(wrapper.emitted('next-action')).toBeTruthy()
    expect(wrapper.emitted('next-action')!.length).toBe(1)
  })
})

// ── T877: workflow-band visibility regression guard ──
//
// An N161/T847 misfix caused the entire workflow band (.wf-section) to disappear; it has since been reverted.
// DocWorkflow.vue root v-if condition:
//   stepStates.length > 0 || (tab.typeCode === 'R' && workflowDecided === false)
//
// Band visibility matrix:
//   | doc type | workflow status             | stepStates   | band   |
//   |----------|-----------------------------|--------------|--------|
//   | R        | undecided (workflowDecided=false) | []       | show   |  <- placeholder path
//   | R        | decided (wf_in_progress)         | length > 0 | show   |
//   | non-R    | pending_review                    | length > 0 | show   |
//   | non-R    | any                               | []         | hide   |  <- intentional, not a regression
//
// Missing .wf-section in S1, S2, or S3 is a band-disappearance regression.

describe('DocWorkflow — band 가시성 회귀 가드 (T877)', () => {
  function mountComp(overrides: Record<string, unknown> = {}) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.doc', typeCode: 'R' },
        workflowDecided: true,
        stepStates: [] as StepState[],
        ...overrides,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  // T877-S1: undecided R (workflowDecided=false, stepStates=[]) shows .wf-section.
  // v-if: false || (typeCode==='R' && workflowDecided===false) → true
  // Regression signal: removing the undecided-R path from v-if hides the band because stepStates=[].
  it('T877-S1: R 미결정 (workflowDecided=false, stepStates=[]) → .wf-section 표시, wf-undecided 플레이스홀더 2개', () => {
    const wrapper = mountComp({
      tab: { id: 'test.r', typeCode: 'R' },
      workflowDecided: false,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.findAll('.wf-undecided').length).toBe(2)
  })

  // T877-S2: decided R + wf_in_progress + pending DS head shows .wf-section.
  // v-if: stepStates.length(4) > 0 → true
  // Regression signal: returning stepStates=[] from resolveWorkflowViewState hides the band.
  it('T877-S2: R 결정 + head=DS pending (stepStates 4개) → .wf-section 표시, DS wf-next-action', () => {
    const wrapper = mountComp({
      tab: { id: 'test.r', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R',  'done'),
        ss('DS', 'highlight'),
        ss('D',  'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.findAll('.wf-step').length).toBe(4)
    expect(wrapper.findAll('.wf-step')[1].classes()).toContain('wf-next-action')
  })

  // T877-S3: non-R DS tab + pending_review + nonempty stepStates shows .wf-section.
  // v-if: stepStates.length(4) > 0 → true
  // Regression signal: receiving [] stepStates for a non-R type hides the band.
  it('T877-S3: DS 탭 + pending_review + stepStates 4개 → .wf-section 표시, DS wf-next-action, R done', () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [
        ss('R',  'done'),
        ss('DS', 'highlight'),
        ss('D',  'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.findAll('.wf-step').length).toBe(4)
    expect(wrapper.findAll('.wf-step')[1].classes()).toContain('wf-next-action')
    expect(wrapper.findAll('.wf-step')[0].classes()).toContain('done')
  })

  // T877-S4: non-R DS tab + stepStates=[] hides .wf-section intentionally.
  // v-if: stepStates.length(0) > 0 (false) || (typeCode==='R'(false) && ...) → false
  // This is intentional hiding; absence of wf-section is correct.
  it('T877-S4: DS 탭 + stepStates=[] → .wf-section 숨김 (의도된 동작, 회귀 아님)', () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'DS' },
      workflowDecided: true,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(false)
  })

  // 0119 B0001 (NR0003 §6-B): decided R/B whose every step was deleted (decided-but-empty).
  // Previously the section + [Edit] button collapsed, stranding the workflow. Now the
  // section stays visible with a recovery hint AND the [Edit] button, so steps can be re-added.
  it('0119-S5: R 결정+빈 시퀀스 (workflowDecided=true, stepStates=[]) → .wf-section + 편집버튼 + 복구 힌트 표시', () => {
    const wrapper = mountComp({
      tab: { id: 'test.empty', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.find('.wf-edit-btn').exists()).toBe(true)        // recovery affordance
    expect(wrapper.find('.wf-empty-recover').exists()).toBe(true)   // hint shown
    expect(wrapper.find('.wf-undecided').exists()).toBe(false)      // NOT the undecided placeholder
  })

  it('0119-S5b: B 결정+빈 시퀀스 → .wf-section + 복구 힌트 표시 (B 루트도 동일)', () => {
    const wrapper = mountComp({
      tab: { id: 'test.emptyb', typeCode: 'B' },
      workflowDecided: true,
      stepStates: [],
    })
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.find('.wf-empty-recover').exists()).toBe(true)
  })
})

// ── 0018 R0001: workflow-strip time-machine — completed step cells are clickable ──
//
// R0001 extends the time-machine (roll back to an earlier step) from the AC-reject-only
// trigger to every completed ('done') step icon in the workflow strip. DocWorkflow makes
// done cells clickable and emits `time-machine` with { index, code }; head/future cells
// keep their existing (or absent) behaviour.

describe('DocWorkflow — 완료 단계 클릭 타임머신 (0018 R0001)', () => {
  function mountComp(overrides: Record<string, unknown> = {}) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.doc', typeCode: 'R' },
        workflowDecided: true,
        stepStates: [] as StepState[],
        ...overrides,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  it('TM1: 완료(done) 단계 클릭 → time-machine { index, code } emit, next-action 미발생', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.ds', typeCode: 'T' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'done'),
        ss('D', 'done'),
        ss('T', 'current'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    // done cells expose the clickable affordance
    expect(steps[1].classes()).toContain('wf-done-clickable')
    await steps[1].trigger('click') // DS (done, index 1)
    const emitted = wrapper.emitted('time-machine')
    expect(emitted).toBeTruthy()
    expect(emitted!.length).toBe(1)
    expect(emitted![0][0]).toEqual({ index: 1, code: 'DS' })
    // a rollback is not a forward action
    expect(wrapper.emitted('next-action')).toBeFalsy()
  })

  it('TM2: index/code가 클릭한 셀과 정확히 일치 (반복 타입 슬롯 식별)', async () => {
    // A design series where D repeats — the emitted index disambiguates the slot.
    const wrapper = mountComp({
      tab: { id: 'test.r', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('D', 'done'),   // index 1 — first D
        ss('T', 'done'),
        ss('D', 'done'),   // index 3 — second D
        ss('AC', 'current'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    await steps[3].trigger('click') // second D
    const emitted = wrapper.emitted('time-machine')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toEqual({ index: 3, code: 'D' })
  })

  it('TM3: future 단계 클릭 → time-machine / next-action 모두 미발생', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.r', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'current'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps[2].classes()).not.toContain('wf-done-clickable')
    await steps[2].trigger('click') // D (future)
    await steps[3].trigger('click') // AC (future)
    expect(wrapper.emitted('time-machine')).toBeFalsy()
    expect(wrapper.emitted('next-action')).toBeFalsy()
  })

  it('TM4: head(current+canNextAction) 클릭 → next-action만, time-machine 미발생', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.r', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R', 'done'),
        ss('DS', 'current'),
        ss('D', 'future'),
        ss('AC', 'future'),
      ],
      canNextAction: true,
    })
    const steps = wrapper.findAll('.wf-step')
    await steps[1].trigger('click') // head
    expect(wrapper.emitted('next-action')).toBeTruthy()
    expect(wrapper.emitted('time-machine')).toBeFalsy()
  })
})

// ── T871: verify removal of DocWorkflow highlighting in wf_done state ──
//
// wf_done makes resolveWorkflowViewState emit mode='info', canNextAction=false,
// and visual='done' / className='done' for every stepState.
// In DocWorkflow.vue:
//   :class="[s.className, s.visual === 'highlight' && canNextAction ? 'wf-current-clickable' : '']"
// visual='done' applies className='done' without wf-next-action.
// canNextAction=false also prevents wf-current-clickable and click events.

describe('DocWorkflow — wf_done 강조 해제 렌더링 (T871)', () => {
  function mountComp(overrides: Record<string, unknown> = {}) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.doc', typeCode: 'R' },
        workflowDecided: true,
        stepStates: [] as StepState[],
        ...overrides,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  // T871-S3: R tab, wf_done; all seven sequence steps are done and canNextAction=false.
  // Expect every cell to have 'done', with no wf-next-action or wf-current-clickable,
  // and no next-action event when any step is clicked.
  it('T871-S3: R tab wf_done (all done, canNextAction=false) → 전 스텝 done 클래스, wf-next-action / wf-current-clickable 미적용, 클릭 이벤트 없음', async () => {
    const wrapper = mountComp({
      tab: { id: 'test.doc', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [
        ss('R',  'done'),
        ss('DS', 'done'),
        ss('D',  'done'),
        ss('P',  'done'),
        ss('L',  'done'),
        ss('T',  'done'),
        ss('TR', 'done'),
      ],
      canNextAction: false,
    })
    const steps = wrapper.findAll('.wf-step')
    expect(steps.length).toBe(7)
    steps.forEach((step) => {
      expect(step.classes()).toContain('done')
      expect(step.classes()).not.toContain('wf-next-action')
      expect(step.classes()).not.toContain('wf-current-clickable')
    })
    // Clicking any step must not emit next-action.
    for (const step of steps) {
      await step.trigger('click')
    }
    expect(wrapper.emitted('next-action')).toBeFalsy()
  })
})
