// T864 — ActionBar mode entry validation
// Verify each mode's entry conditions (resolveWorkflowViewState) and visible actions (ReviewActionBar rendering).
// Memory: [feedback_actionbar_d030_guard], [feedback_actionbar_always_shows]
// Modes: workflow / info / rejected / review(decide)

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

const BASE_PROPS = {
  docId: 'proj.grp.0001.0001-R',
  projectId: 'proj',
  groupId: 'grp',
  docRef: 'GRP-0001.0001-R',
  reviewStatus: null as string | null,
}

function resolve(overrides: Partial<WorkflowViewInput>) {
  return resolveWorkflowViewState({ ...BASE_INPUT, ...overrides })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── S1: workflow mode ─────────────────────────────────────────────────────────
// Entry condition: R tab with tabReviewStatus lacking the 'wf_' prefix (undecided).
// Visible action: one [Decide Workflow] button; no Approve or Reject.

describe('T864-S1 — workflow mode: R undecided', () => {
  it('pure: R + null reviewStatus → mode=workflow, canNextAction=false', () => {
    const result = resolve({ tabTypeCode: 'R', tabReviewStatus: null })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
  })

  it('pure: R + non-wf_ reviewStatus (pending_review) → mode=workflow', () => {
    const result = resolve({ tabTypeCode: 'R', tabReviewStatus: 'pending_review' })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
  })

  it('component: mode=workflow → [Decide Workflow] btn; Approve / Reject / Next 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docType: 'R', reviewStatus: 'pending_review', mode: 'workflow' },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
    expect(wrapper.text()).not.toContain('Next')
  })
})

// ── S2: info mode ─────────────────────────────────────────────────────────────
// Entry condition A: R tab with wf_done (workflow complete).
// Entry condition B: M tab with nextStepExists=false (no next step).
// Visible actions: none. The action area is empty and renders no buttons.

describe('T864-S2 — info mode: no pending action', () => {
  it('pure: R + wf_done → mode=info, canNextAction=false', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_done',
      headType: 'TR',
      headStatus: 'done',
    })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepCode).toBeNull()
  })

  it('pure: M + nextStepExists=false → mode=info, canNextAction=false', () => {
    const result = resolve({
      tabTypeCode: 'M',
      nextStepExists: false,
      headType: null,
      headStatus: null,
    })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
  })

  it('component: mode=info → sfb-actions div 존재하나 버튼 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...BASE_PROPS, docType: 'R', reviewStatus: 'wf_done', mode: 'info' },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-actions').exists()).toBe(true)
    expect(wrapper.find('.sfb-actions button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── S3: rejected mode (R head doc rejected) ───────────────────────────────────
// Entry condition: R tab with headDocReviewStatus='rejected'.
// Visible action: rework toolbar (.sfb-actions--rework); no Approve or Reject.

describe('T864-S3 — rejected mode: R head doc rejected', () => {
  it('pure: R + wf_in_progress + headDocReviewStatus=rejected → mode=rejected', () => {
    const result = resolve({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'D',
      headStatus: 'in_progress',
      headDocId: 'proj.grp.0001.0003-D',
      headDocReviewStatus: 'rejected',
      nextStepExists: true,
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.headDocId).toBe('proj.grp.0001.0003-D')
    expect(result.headDocLabel).toBe('D')
  })

  it('component: R tab mode=rejected + headDocId set → isViewingPastDoc 분기, 헤드 doc 이동 버튼 렌더 (rework toolbar 아님)', () => {
    // On the R tab, the head document (D) is rejected: viewedDocId(R) != headDocId(D).
    // This makes isViewingPastDoc=true, so only the "Go to D" navigation button is shown.
    // The rework toolbar appears on the rejected D document tab itself (covered by S4).
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'rejected',
        headDocId: 'proj.grp.0001.0003-D',
        headDocLabel: 'D',
        viewedDocId: 'proj.grp.0001.0001-R',
      },
      global: { plugins: [i18n] },
    })
    const navBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(navBtn.exists()).toBe(true)
    expect(navBtn.text()).toContain('0003-D 로 이동')
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
  })
})

// ── S4: rejected mode (non-R doc rejected) ────────────────────────────────────
// Entry condition: non-R tab with tabReviewStatus='rejected'.
// Visible action: rework toolbar; no standard Approve or Reject.

describe('T864-S4 — rejected mode: non-R doc rejected', () => {
  it('pure: D + rejected → mode=rejected, canNextAction=false', () => {
    const result = resolve({
      tabTypeCode: 'D',
      tabReviewStatus: 'rejected',
      headType: 'D',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
  })

  it('pure: TS + rejected → mode=rejected (모든 non-R 타입 동일 경로)', () => {
    const result = resolve({
      tabTypeCode: 'TS',
      tabReviewStatus: 'rejected',
      headType: 'TS',
      headStatus: 'in_progress',
      nextStepExists: false,
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
  })

  it('component: D mode=rejected → rework toolbar (.sfb-actions--rework) 렌더; Approve 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0003-D',
        docType: 'D',
        reviewStatus: 'rejected',
        mode: 'rejected',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── S5: review mode (decide: approve / reject) ────────────────────────────────
// Entry condition: non-R tab with a tabReviewStatus other than approved/rejected, such as pending_review or revised.
// Visible actions: [Approve] and [Reject]; no workflow or next button.

describe('T864-S5 — review mode: approver decision (decide)', () => {
  it('pure: D + pending_review → mode=review, canNextAction=false', () => {
    const result = resolve({
      tabTypeCode: 'D',
      tabReviewStatus: 'pending_review',
      headType: 'D',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(result.mode).toBe('review')
    expect(result.canNextAction).toBe(false)
  })

  it('pure: DS + revised → mode=review (재검토 상태도 review 분기)', () => {
    const result = resolve({
      tabTypeCode: 'DS',
      tabReviewStatus: 'revised',
      headType: 'DS',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(result.mode).toBe('review')
    expect(result.canNextAction).toBe(false)
  })

  it('component: D mode=review → Approve + Reject 버튼; workflow / next 버튼 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0003-D',
        docType: 'D',
        reviewStatus: 'pending_review',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(false)
  })
})
