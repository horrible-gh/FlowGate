import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import type { StepState } from '@main/workflow/workflowViewState'

// flowgate.default.0395 T0021 — "[워크플로 시퀀스] 에 나와야하는거 아닌가?"
//
// NR0020 §1 measured two defects in the old placement of [작업계획 생성] (the action
// bar): it disappeared exactly where a plan was still wanted (wf_done / rejected), and
// it appeared on documents the server answers with 422 (any approved non-R/B step).
// The button now lives in the [워크플로 시퀀스] section, and 작업계획 is a step type the
// sequence can hold, so these tests pin both halves.

function ss(code: string, visual: StepState['visual']): StepState {
  const className = {
    done: 'done',
    highlight: 'wf-next-action dip-step-clickable dip-step-active',
    rejected: 'wf-rejected dip-step-rejected',
    current: 'current',
    future: 'future dip-step-disabled',
  }[visual]
  return { code, visual, className, iconClass: 'circle' }
}

function mountComp(overrides: Record<string, unknown> = {}) {
  return mount(DocWorkflow, {
    props: {
      tab: { id: 'flowgate.default.0395.0001-R', typeCode: 'R' },
      workflowDecided: true,
      stepStates: [] as StepState[],
      ...overrides,
    } as any,
    global: { plugins: [i18n], stubs: { WorkflowDecisionModal: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
})

describe('0395 T0036 — 작업계획 entry point is only the WP sequence cell', () => {
  it.each([
    [ss('WP', 'highlight')],
    [ss('T', 'done')],
    [ss('T', 'rejected')],
  ])('never renders a title-row [작업계획 생성] button', (state) => {
    const wrapper = mountComp({ stepStates: [ss('R', 'done'), state], canNextAction: true })
    expect(wrapper.find('.wf-wp-btn').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(i18n.global.t('main.review_action_bar.btn_create_work_plan'))
  })

  it('clicking the WP step opens the plan dialog instead of the generic next step', async () => {
    // D0007 §3.1 결정 3: the related-document route is deliberately not reused for WP.
    const wrapper = mountComp({
      tab: { id: 'flowgate.default.0395.0011-T', typeCode: 'T' },
      parentRDocId: 'flowgate.default.0395.0001-R',
      stepStates: [ss('R', 'done'), ss('WP', 'highlight'), ss('AC', 'future')],
      canNextAction: true,
    })
    await wrapper.findAll('.wf-step')[1].trigger('click')
    expect(wrapper.emitted('next-action')).toBeUndefined()
    expect(wrapper.emitted('create-work-plan')?.[0]).toEqual([
      { docId: 'flowgate.default.0395.0001-R' },
    ])
  })

  it('a non-WP head still emits the generic next-step action', async () => {
    const wrapper = mountComp({
      tab: { id: 'flowgate.default.0395.0001-R', typeCode: 'R' },
      stepStates: [ss('R', 'done'), ss('T', 'highlight')],
      canNextAction: true,
    })
    await wrapper.findAll('.wf-step')[1].trigger('click')
    expect(wrapper.emitted('next-action')).toHaveLength(1)
    expect(wrapper.emitted('create-work-plan')).toBeUndefined()
  })
})

describe('0395 T0021 — the action bar no longer carries the button', () => {
  const actionBar = readFileSync(
    join(process.cwd(), 'src/main/components/ReviewActionBar.vue'),
    'utf8',
  )
  const mainPanel = readFileSync(
    join(process.cwd(), 'src/main/components/MainPanel.vue'),
    'utf8',
  )

  it('ReviewActionBar reuses the label for its existing create-empty action, without restoring a separate button/event', () => {
    expect(actionBar).toMatch(/const nextCreateLabelKey/)
    expect(actionBar).not.toMatch(/'create-work-plan':\s*\[\]/)
  })

  it('MainPanel listens for it on DocWorkflow', () => {
    expect(mainPanel).toMatch(/@create-work-plan="onCreateWorkPlan\(tab\.id, \$event\.docId\)"/)
  })
})
