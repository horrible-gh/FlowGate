// T868: verify "current step" highlighting for an undecided (wfDecided=false) R document.
// Covers the workflowViewState R-tab wfDecided branch and DocWorkflow .wf-step.wf-undecided mapping.
// Tracks the N163 progress-area regression; a gray fallback is a regression.
// Memory: [feedback_actionbar_always_shows]

import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
  type StepState,
} from '@main/workflow/workflowViewState'
import DocWorkflow from '@main/components/DocWorkflow.vue'

function ss(code: string, visual: 'done' | 'highlight' | 'rejected' | 'current' | 'future'): StepState {
  const CLS: Record<string, string> = {
    done: 'done',
    highlight: 'wf-next-action dip-step-clickable dip-step-active',
    rejected: 'wf-rejected dip-step-rejected',
    current: 'current',
    future: 'future dip-step-disabled',
  }
  const ICN: Record<string, string> = {
    done: 'fa-solid fa-circle-check',
    highlight: 'fa-regular fa-circle',
    rejected: 'fa-solid fa-circle-xmark',
    current: 'fa-regular fa-circle-dot',
    future: 'fa-regular fa-circle',
  }
  return { code, visual, className: CLS[visual], iconClass: ICN[visual] }
}

const BASE_INPUT: WorkflowViewInput = {
  tabTypeCode: null,
  tabReviewStatus: null,
  workflowSteps: [],
  headType: null,
  headStatus: null,
  headDocId: null,
  headDocReviewStatus: null,
  nextStepExists: false,
  qStatus: null,
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── T868-S1 ── workflowViewState: undecided R document -> mode=workflow, all stepStates future ──
// Covers IDs such as test.teseu.0003.0001-R with tabReviewStatus=null before a decision.
// The requirements-definition step (R) must not become highlight/current; no gray-fallback regression.

describe('T868-S1 — workflowViewState: 미결정 R-doc → mode=workflow, stepStates 전부 future', () => {
  it('tabReviewStatus=null → mode=workflow, canNextAction=false, 모든 stepState visual=future', () => {
    const result = resolveWorkflowViewState({
      ...BASE_INPUT,
      tabTypeCode: 'R',
      tabReviewStatus: null,
      workflowSteps: ['R', 'DS', 'D', 'T', 'AC'],
      headType: null,
      headStatus: null,
    })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    result.stepStates.forEach((s) => {
      expect(s.visual).toBe('future')
      expect(s.className).toContain('future')
      expect(s.className).not.toContain('wf-next-action')
    })
  })

  it('tabReviewStatus="" (빈 문자열) → mode=workflow (wf_ 접두 없음)', () => {
    const result = resolveWorkflowViewState({
      ...BASE_INPUT,
      tabTypeCode: 'R',
      tabReviewStatus: '',
      workflowSteps: ['R', 'DS', 'D'],
    })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
    result.stepStates.forEach((s) => expect(s.visual).toBe('future'))
  })
})

// ── T868-S2 ── workflowViewState: all non-wf_ statuses -> mode=workflow regression guard ──
// Any value without the wf_ prefix must preserve the undecided path (mode=workflow).

describe('T868-S2 — workflowViewState: non-wf_ tabReviewStatus 전부 mode=workflow 회귀 가드', () => {
  it.each([null, '', 'pending', 'pending_review', 'revised', 'some_arbitrary_value'])(
    'tabReviewStatus=%s → mode=workflow, stepStates 전부 future',
    (status) => {
      const result = resolveWorkflowViewState({
        ...BASE_INPUT,
        tabTypeCode: 'R',
        tabReviewStatus: status as string | null,
        workflowSteps: ['R', 'DS', 'D', 'T'],
        headType: 'DS',
        headStatus: 'pending',
      })
      expect(result.mode).toBe('workflow')
      expect(result.canNextAction).toBe(false)
      result.stepStates.forEach((s) => {
        expect(s.visual).toBe('future')
        expect(s.className).not.toContain('wf-next-action')
      })
    },
  )
})

// ── T868-S3 ── DocWorkflow: undecided R -> highlighted wf-undecided placeholders ──
// workflowDecided=false shows two .wf-step.wf-undecided elements and a visible .wf-section.
// Prevent the gray-fallback regression: placeholder steps must not have future/dip-step-disabled classes.

describe('T868-S3 — DocWorkflow: R 미결정 → wf-undecided 플레이스홀더 강조, future 클래스 없음', () => {
  function mountUndecided(stepStates: StepState[] = []) {
    return mount(DocWorkflow, {
      props: {
        tab: { id: 'test.teseu.0003.0001-R', typeCode: 'R' },
        workflowDecided: false,
        stepStates,
        canNextAction: false,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
  }

  it('wf-section 표시, wf-undecided 플레이스홀더 2개 존재', () => {
    const wrapper = mountUndecided()
    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.findAll('.wf-undecided').length).toBe(2)
    expect(wrapper.findAll('.wf-step').length).toBe(2)
  })

  it('플레이스홀더 step은 future / dip-step-disabled 클래스 미보유 (회색 fallback 회귀 없음)', () => {
    const wrapper = mountUndecided()
    wrapper.findAll('.wf-step').forEach((step) => {
      expect(step.classes()).not.toContain('future')
      expect(step.classes()).not.toContain('dip-step-disabled')
    })
  })

  it('플레이스홀더 step에 wf-next-action 없음 — 미결정 중 spurious 강조 없음', () => {
    const wrapper = mountUndecided()
    wrapper.findAll('.wf-step').forEach((step) => {
      expect(step.classes()).not.toContain('wf-next-action')
    })
  })

  it('stepStates에 future 배열이 전달돼도 컴포넌트는 플레이스홀더 렌더 (stepStates 무시)', () => {
    const futureSteps = [ss('R', 'future'), ss('DS', 'future'), ss('D', 'future')]
    const wrapper = mountUndecided(futureSteps)
    expect(wrapper.findAll('.wf-undecided').length).toBe(2)
    // The stepStates v-for must not render: expect two placeholder wf-units, not three stepState units.
    expect(wrapper.findAll('.wf-unit').length).toBe(2)
  })
})

// ── T868-S4 ── Regression: no wf-undecided residue after deciding R (wfDecided=true) ──
// After the R decision, the undecided placeholders disappear and normal stepStates rendering takes over.
// Guards against undecided highlighting remaining after the decision.

describe('T868-S4 — 회귀: R 결정(wfDecided=true) → wf-undecided 없음, stepStates 정상 렌더', () => {
  it('workflowDecided=true → .wf-undecided 없음, head step wf-next-action 표시', () => {
    const wrapper = mount(DocWorkflow, {
      props: {
        tab: { id: 'test.teseu.0003.0001-R', typeCode: 'R' },
        workflowDecided: true,
        stepStates: [
          ss('R', 'done'),
          ss('DS', 'highlight'),
          ss('D', 'future'),
          ss('T', 'future'),
        ],
        canNextAction: true,
      } as any,
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })
    expect(wrapper.find('.wf-undecided').exists()).toBe(false)
    expect(wrapper.findAll('.wf-step').length).toBe(4)
    expect(wrapper.findAll('.wf-step')[1].classes()).toContain('wf-next-action') // DS highlighted
    expect(wrapper.findAll('.wf-step')[0].classes()).toContain('done')           // R done
  })
})
