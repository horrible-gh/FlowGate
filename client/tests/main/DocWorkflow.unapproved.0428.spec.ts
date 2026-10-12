import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import type { StepState } from '@main/workflow/workflowViewState'

const wrappers: ReturnType<typeof mount>[] = []
const steps: StepState[] = [
  { code: 'TSR', visual: 'done', className: 'done', iconClass: 'check-circle' },
  { code: 'TSR', visual: 'done', className: 'done', iconClass: 'check-circle', completionLabel: 'proceeded_unapproved' },
  { code: 'TR2', visual: 'current', className: 'current', iconClass: 'radio-button' },
]
function strip(extra = {}) {
  const w = mount(DocWorkflow, {
    props: { tab: { id: 'flowgate.default.0428.0001-R', typeCode: 'R' }, workflowDecided: true,
      stepStates: steps, ...extra } as any,
    global: { plugins: [i18n], stubs: { WorkflowDecisionModal: true } },
  })
  wrappers.push(w)
  return w
}
beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))

describe('T0018 compact unapproved workflow', () => {
  it.each([['ko', '미승인 진행'], ['en', 'Without approval']])('uses a tooltip and accessible label in %s without adding visible text', (locale, hint) => {
    i18n.global.locale.value = locale as 'ko' | 'en'
    const w = strip()
    const cells = w.findAll('.wf-step')
    expect(cells[0].classes()).not.toContain('wf-unapproved-step')
    expect(cells[1].classes()).toContain('wf-unapproved-step')
    expect(cells[2].classes()).not.toContain('wf-unapproved-step')
    expect(cells[1].attributes('title')).toContain(hint)
    expect(cells[1].attributes('aria-label')).toContain(hint)
    expect(cells[1].text()).toBe(cells[0].text())
    expect(w.find('[data-test="wf-unapproved"]').exists()).toBe(false)
    expect(w.findAll('.wf-step')).toHaveLength(3)
  })
  it('preserves time-machine clicks and the original tooltip', async () => {
    i18n.global.locale.value = 'ko'
    const w = strip()
    const cell = w.findAll('.wf-step')[1]
    expect(cell.attributes('title')).toContain(i18n.global.t('main.doc_workflow.time_machine_hint'))
    await cell.trigger('click')
    expect(w.emitted('time-machine')?.[0]).toEqual([{ index: 1, code: 'TSR' }])
  })
  it('keeps the status hint visible in read-only mode and prevents mutations', async () => {
    i18n.global.locale.value = 'en'
    const w = strip({ readOnly: true })
    const cell = w.findAll('.wf-step')[1]
    expect(cell.attributes('title')).toBe('Without approval')
    await cell.trigger('click')
    expect(w.emitted('time-machine')).toBeUndefined()
  })
})
