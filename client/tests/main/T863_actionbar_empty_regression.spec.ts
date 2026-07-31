// T863 — ActionBar empty-state regression guard
// Verifies action bar always renders ≥1 actionable element for every active
// workflow state. Empty bar / mode=null / mode='info' alone = regression.
// Memory: [feedback_actionbar_empty_is_regression], [feedback_actionbar_always_shows]
// Scope: commit 0a0dd2d (action bar policy unification) post-validation.

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
} from '../../src/main/workflow/workflowViewState'

// Modes that produce an empty action div — any active state resolving to these is
// a regression.
const EMPTY_MODES = new Set(['info', 'sequence-complete'])

const BASE_INPUT: WorkflowViewInput = {
  tabTypeCode: null,
  tabReviewStatus: null,
  workflowSteps: ['M', 'DS', 'D', 'T', 'TR'],
  headType: null,
  headStatus: null,
  headDocId: null,
  headDocReviewStatus: null,
  nextStepExists: false,
  qStatus: null,
}

function resolve(overrides: Partial<WorkflowViewInput>) {
  return resolveWorkflowViewState({ ...BASE_INPUT, ...overrides })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── Level 1: resolveWorkflowViewState pure-function contract ─────────────────
// Verifies mode never falls into an empty bucket for actionable input states.

describe('T863 — resolveWorkflowViewState: active states must not resolve to empty mode', () => {

  it('T863-S1 — R decided + head=pending (wf_in_progress): mode is next, not info/empty', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
  })

  it('T863-S2 — R decided + head=in_progress: mode is next (disabled), not info/empty', () => {
    // PM decision (D031 §9): R branch stays next even when head is in_progress.
    // A regression would render an empty bar, blocking the user entirely.
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'D',
      headStatus: 'in_progress',
      headDocId: 'proj.grp.0001.0003-D',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(false)  // disabled but present
  })

  it('T863-S3 — D type pending_review: mode is review, not info/empty', () => {
    const result = resolve({
      tabTypeCode: 'D',
      tabReviewStatus: 'pending_review',
      headType: 'D',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('review')
  })

  it('T863-S4 — D type rejected: mode is rejected (rework toolbar), not info/empty', () => {
    const result = resolve({
      tabTypeCode: 'D',
      tabReviewStatus: 'rejected',
      headType: 'D',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('rejected')
  })

  it('T863-S5 — DS type approved + next step exists: mode is next, not info/empty', () => {
    // A regression here would return mode=info (no next step guard firing incorrectly).
    const result = resolve({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      headType: 'D',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
  })

  it('T863-S6 — M type with next step: mode is next, not info/empty', () => {
    const result = resolve({
      tabTypeCode: 'M',
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(EMPTY_MODES.has(result.mode as string)).toBe(false)
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
  })
})

// ── Level 2: ReviewActionBar component rendering ─────────────────────────────
// Verifies the rendering chain: mode → component → DOM has ≥1 actionable element.
// Tests mount the component with the mode that resolveWorkflowViewState produces.

describe('T863 — ReviewActionBar: component renders ≥1 actionable element for active modes', () => {

  const BASE_PROPS = {
    docId: 'proj.grp.0001.0003-D',
    projectId: 'proj',
    groupId: 'grp',
    docRef: 'GRP-0001.0003-D',
    reviewStatus: null as string | null,
  }

  function hasActionableElement(wrapper: ReturnType<typeof mount>): boolean {
    return (
      wrapper.find('.sfb-actions button').exists() ||
      wrapper.find('.sfb-actions .sfb-hint').exists()
    )
  }

  it('T863-S1 component — R wf_in_progress head=pending: [Next step] button rendered', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docId: 'proj.grp.0001.0001-R', docType: 'R',
               reviewStatus: 'wf_in_progress', mode: 'next', canNextAction: true,
               nextStepLabel: 'DS' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    expect(wrapper.find('.sfb-actions button.btn-primary').exists()).toBe(true)
    expect(wrapper.find('.sfb-actions button.btn-primary').attributes('disabled')).toBeUndefined()
  })

  it('T863-S2 component — R wf_in_progress head=in_progress: [Next step] present; dropdown carries no proceed item (0366 T0007 removed it from the action bar)', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docId: 'proj.grp.0001.0001-R', docType: 'R',
               reviewStatus: 'wf_in_progress', mode: 'next', canNextAction: false,
               nextStepLabel: 'D' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    // The main button now opens the dropdown and is always enabled (R0001 ③-a).
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
    // 0366 T0007: [다음 단계 진행] is no longer offered here at all — the canNextAction
    // guard now only gates the workflow strip's current-step cell (DocWorkflow.vue).
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const items = wrapper.findAll('.ab-split-dd .ab-split-item')
    expect(items.some(i => i.text().includes('Proceed to Next Step'))).toBe(false)
  })

  it('T863-S3 component — D pending_review: Approve + Reject buttons rendered', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docType: 'D', reviewStatus: 'pending_review', mode: 'review' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })

  it('T863-S4 component — D rejected: rework toolbar rendered (≥1 button)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docType: 'D', reviewStatus: 'rejected', mode: 'rejected' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(true)
    expect(wrapper.findAll('.sfb-actions--rework button').length).toBeGreaterThanOrEqual(1)
  })

  it('T863-S5 component — DS approved + next step: [Next step] button rendered (not info empty)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docId: 'proj.grp.0001.0002-DS', docType: 'DS',
               reviewStatus: 'approved', mode: 'next', canNextAction: true,
               nextStepLabel: 'D' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('D')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('T863-S6 component — M next step: [Next step] button rendered', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docId: 'proj.grp.0001.0000-M', docType: 'M',
               reviewStatus: null, mode: 'next', canNextAction: true,
               nextStepLabel: 'DS' },
      global: { plugins: [i18n] },
    })
    expect(hasActionableElement(wrapper)).toBe(true)
    expect(wrapper.find('.sfb-actions button.btn-primary').exists()).toBe(true)
  })
})
