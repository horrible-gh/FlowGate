import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
} from '@main/workflow/workflowViewState'

const baseInput: WorkflowViewInput = {
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

describe('B workflow root', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
  })

  it('uses the workflow-decision mode while undecided', () => {
    const result = resolveWorkflowViewState({
      ...baseInput,
      tabTypeCode: 'B',
    })

    expect(result.mode).toBe('workflow')
    expect(result.currentStepCode).toBe('B')
  })

  it('uses the next-step mode after a decision', () => {
    const result = resolveWorkflowViewState({
      ...baseInput,
      tabTypeCode: 'B',
      tabReviewStatus: 'wf_in_progress',
      headType: 'T',
      headStatus: 'pending',
    })

    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
  })

  it('renders the B undecided workflow placeholder', () => {
    const wrapper = mount(DocWorkflow, {
      props: {
        tab: { id: 'flowgate.default.0001.0001-B', typeCode: 'B' },
        workflowDecided: false,
        stepStates: [],
      },
      global: {
        plugins: [i18n],
        stubs: { WorkflowDecisionModal: true },
      },
    })

    expect(wrapper.find('.wf-section').exists()).toBe(true)
    expect(wrapper.find('.wf-undecided.current').text()).toContain('B')
  })
})
