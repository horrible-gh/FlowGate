// T865 — ActionBar decided ↔ undecided transition validation
// Verifies that resolveWorkflowViewState produces the correct mode and that
// ReviewActionBar renders the correct actions at each side of the transition
// boundary and after the reverse transition.
// Memory: [feedback_actionbar_always_shows], [feedback_actionbar_d030_guard]
// Key boundary: wfDecided = tabReviewStatus.startsWith('wf_') or a materialized head.

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
} from '../../src/main/workflow/workflowViewState'

const BASE_INPUT: WorkflowViewInput = {
  tabTypeCode: 'R',
  tabReviewStatus: null,
  workflowSteps: ['M', 'DS', 'D', 'T', 'TR'],
  headType: null,
  headStatus: null,
  headDocId: null,
  headDocReviewStatus: null,
  nextStepExists: true,
  qStatus: null,
}

const BASE_PROPS = {
  docId: 'proj.grp.0001.0001-R',
  projectId: 'proj',
  groupId: 'grp',
  docRef: 'GRP-0001.0001-R',
  reviewStatus: null as string | null,
  docType: 'R',
}

function resolve(overrides: Partial<WorkflowViewInput>) {
  return resolveWorkflowViewState({ ...BASE_INPUT, ...overrides })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── S1: undecided state ──────────────────────────────────────────────────────
// Entry condition: R tab, tabReviewStatus has no 'wf_' prefix and no materialized head.
// Expected action: [Decide Workflow] button only. No Next / Approve / Reject.
// D030 §4 #1. stepStates must all be 'future' (wfDecided=false → allFutureSS).

describe('T865-S1 — R undecided: mode=workflow, [Decide Workflow] only', () => {
  it('pure: R + null tabReviewStatus → mode=workflow, canNextAction=false, all stepStates future', () => {
    const result = resolve({ tabTypeCode: 'R', tabReviewStatus: null, headType: null, headStatus: null })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    expect(result.nextStepActive).toBe(false)
    expect(result.stepStates.every(s => s.visual === 'future')).toBe(true)
  })

  it('pure: R + empty-string tabReviewStatus → mode=workflow (no wf_ prefix)', () => {
    const result = resolve({ tabTypeCode: 'R', tabReviewStatus: '', headType: null, headStatus: null })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
  })

  it('pure: R + pending_review tabReviewStatus → mode=workflow (non-wf_ status is undecided)', () => {
    const result = resolve({ tabTypeCode: 'R', tabReviewStatus: 'pending_review', headType: null, headStatus: null })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
  })

  it('component: mode=workflow → [Decide Workflow] button; Next / Approve / Reject absent', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, reviewStatus: 'pending_review', mode: 'workflow' },
      global: { plugins: [i18n] },
    })
    const decideBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(decideBtn.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Next')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── S2: undecided → decided (forward transition) ─────────────────────────────
// Entry condition after transition: R tab, tabReviewStatus='wf_in_progress', headStatus=pending.
// Expected: mode flips from 'workflow' to 'next', canNextAction=true,
//           [Next step] button enabled, [Decide Workflow] absent.
// D030 §4 #2. stepStates: steps before headIndex → done, head → highlight, rest → future.

describe('T865-S2 — undecided → decided: mode workflow → next, [Next step] enabled', () => {
  it('pure: before (null) → mode=workflow; after (wf_in_progress + head pending) → mode=next, canNextAction=true', () => {
    const before = resolve({ tabTypeCode: 'R', tabReviewStatus: null, headType: null, headStatus: null })
    expect(before.mode).toBe('workflow')
    expect(before.canNextAction).toBe(false)

    const after = resolve({ tabTypeCode: 'R', tabReviewStatus: 'wf_in_progress', headType: 'DS', headStatus: 'pending', headDocId: null })
    expect(after.mode).toBe('next')
    expect(after.canNextAction).toBe(true)
    expect(after.highlightStepCode).toBe('DS')
    expect(after.nextStepCode).toBe('DS')
    expect(after.nextStepActive).toBe(true)
  })

  it('pure: decided → stepStates has highlight at headIndex, not all future', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: ['M', 'DS', 'D', 'T', 'TR'],
      headType: 'DS',
      headStatus: 'pending',
    })
    const dsState = result.stepStates.find(s => s.code === 'DS')
    expect(dsState?.visual).toBe('highlight')
    expect(dsState?.className).toContain('wf-next-action')
    const mState = result.stepStates.find(s => s.code === 'M')
    expect(mState?.visual).toBe('done')
  })

  it('component: mode=next, canNextAction=true → [Next step DS] button enabled; no [Decide Workflow]', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, reviewStatus: 'wf_in_progress', mode: 'next', canNextAction: true, nextStepLabel: 'DS' },
      global: { plugins: [i18n] },
    })
    const nextBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(nextBtn.exists()).toBe(true)
    expect(nextBtn.attributes('disabled')).toBeUndefined()
    expect(nextBtn.text()).toContain('DS')
    expect(wrapper.text()).not.toContain('Decide')
    expect(wrapper.text()).not.toContain('Approve')
  })
})

// ── S3: decided → undecided (reverse transition) ─────────────────────────────
// Verifies that an R doc can revert to undecided only when both decision signals
// are absent: no wf_ prefix and no materialized workflow head.
// This guards against a regression where a formerly-decided R doc cannot be
// re-presented with the [Decide Workflow] action after a status reset.

describe('T865-S3 — decided → undecided (reverse): mode reverts to workflow', () => {
  it('pure: wf_in_progress (decided) → mode=next; same input with revised (non-wf_) → mode=workflow', () => {
    const decided = resolve({
      tabTypeCode: 'R', tabReviewStatus: 'wf_in_progress',
      headType: null, headStatus: null,
    })
    expect(decided.mode).toBe('next')

    const reverted = resolve({
      tabTypeCode: 'R', tabReviewStatus: 'revised',
      headType: null, headStatus: null,
    })
    expect(reverted.mode).toBe('workflow')
    expect(reverted.canNextAction).toBe(false)
    expect(reverted.stepStates.every(s => s.visual === 'future')).toBe(true)
  })

  it('pure: any wf_* prefix (wf_done is separate branch, wf_in_progress) → decided; absent prefix → undecided', () => {
    const decided = resolve({ tabTypeCode: 'R', tabReviewStatus: 'wf_in_progress', headType: 'T', headStatus: 'pending' })
    expect(decided.mode).not.toBe('workflow')

    const undecided = resolve({ tabTypeCode: 'R', tabReviewStatus: 'approved', headType: null, headStatus: null })
    expect(undecided.mode).toBe('workflow')
  })

  it('component: mode=workflow after reverse → [Decide Workflow] re-appears; [Next step] absent', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, reviewStatus: 'revised', mode: 'workflow' },
      global: { plugins: [i18n] },
    })
    const decideBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(decideBtn.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Next')
    expect(wrapper.text()).not.toContain('Approve')
  })
})

// ── S4: decided + head in_progress (D030 §3 guard) ───────────────────────────
// Entry condition: R decided, headStatus=in_progress (step already started).
// Expected: mode=next, canNextAction=false — button rendered but DISABLED.
// [feedback_actionbar_always_shows]: button must remain visible (not hidden).
// [feedback_actionbar_d030_guard]: guard = disable, not remove.

describe('T865-S4 — decided + head in_progress: [Next step] disabled (guard), not hidden', () => {
  it('pure: R + wf_in_progress + head in_progress → mode=next, canNextAction=false', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'D',
      headStatus: 'in_progress',
      headDocId: 'proj.grp.0001.0003-D',
      headDocReviewStatus: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBe('D')
    expect(result.headDocId).toBe('proj.grp.0001.0003-D')
  })

  it('pure: head=in_progress → headStep visual=highlight (still shown as active in strip)', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: ['M', 'DS', 'D', 'T', 'TR'],
      headType: 'D',
      headStatus: 'in_progress',
    })
    const dState = result.stepStates.find(s => s.code === 'D')
    expect(dState?.visual).toBe('highlight')
    expect(dState?.className).toContain('wf-next-action')
  })

  it('component: mode=next, canNextAction=false → next button present (opens dropdown); proceed item disabled, not hidden (R0001 ③-a)', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, reviewStatus: 'wf_in_progress', mode: 'next', canNextAction: false, nextStepLabel: 'D' },
      global: { plugins: [i18n] },
    })
    const nextBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(nextBtn.exists()).toBe(true)
    expect(nextBtn.text()).toContain('D')
    // R0001 ③-a: the main button opens the dropdown and is no longer disabled. The
    // guard (canNextAction=false) now disables the in-dropdown "Proceed" item, so the
    // next step still cannot be started while the head doc is in progress.
    expect(nextBtn.attributes('disabled')).toBeUndefined()
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const proceedItem = wrapper
      .findAll('.ab-split-dd .ab-split-item')
      .find(i => i.text().includes('Proceed to Next Step'))
    expect(proceedItem).toBeTruthy()
    expect(proceedItem!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Decide')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})
