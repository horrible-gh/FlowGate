// T866: verify ActionBar compliance with each clause in the D030 section 4 matrix.
// Check that document type plus workflow status yields the expected action set.
// Each scenario corresponds to one row in the section 4.1 matrix.
// Memory: [feedback_actionbar_d030_guard]

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

const BASE_PROPS = {
  docId: 'proj.grp.0001.0001-R',
  projectId: 'proj',
  groupId: 'grp',
  docRef: 'GRP-0001.0001-R',
  reviewStatus: null as string | null,
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── T866-S1 ── D030 section 4 #1: undecided R workflow -> [Decide Workflow] ──
// Entry condition: docType=R, mode=workflow (wfDecided=false).
// Expected action: one [Decide Workflow] button; no Next, Approve, or Reject.

describe('T866-S1 — D030 §4 #1: R 미결정 → [워크플로결정]', () => {
  it('R + mode=workflow → [Decide Workflow] 버튼만, Approve/Reject/Next 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0001-R',
        docType: 'R',
        reviewStatus: null,
        mode: 'workflow',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
    expect(wrapper.text()).not.toContain('Next')
  })
})

// ── T866-S2 ── D030 section 4 #2: decided R + pending head -> enabled [Next step] ──
// Entry condition: docType=R, mode=next, canNextAction=true.
// Expected action: enabled [Next step] button; no Approve or Reject.

describe('T866-S2 — D030 §4 #2: R 결정 + head=pending → [다음단계진행] 활성', () => {
  it('R + mode=next + canNextAction=true → [Next step DS] 활성 버튼, Approve/Reject 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'DS',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.text()).toContain('DS')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── T866-S3 ── D030 section 4 #3: decided R + in-progress head -> disabled [Next step] ──
// Entry condition: docType=R, mode=next, canNextAction=false.
// Expected action: the proceed action is visible but disabled; the guard disables rather
// than removes it. [feedback_actionbar_d030_guard]: unmet guard conditions must disable,
// never hide, the proceed action.
// R0001 ③-a relocates that guard: the main button now opens the dropdown (always
// enabled) and the "Proceed to Next Step" dropdown item carries the disabled state.

describe('T866-S3 — D030 §4 #3: R 결정 + head=in_progress → [다음단계] 진행 비활성', () => {
  it('R + mode=next + canNextAction=false → 메인버튼 표시(드롭다운 오픈), 진행 항목 disabled, Approve/Reject 없음', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: false,
        nextStepLabel: 'D',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.text()).toContain('D')
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const proceedItem = wrapper
      .findAll('.ab-split-dd .ab-split-item')
      .find(i => i.text().includes('Proceed to Next Step'))
    expect(proceedItem).toBeTruthy()
    expect(proceedItem!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── T866-S4 ── D030 section 4 #4: M -> [Next step] or completed (info) ──
// Entry condition A: docType=M, mode=next, nextStepLabel exists -> show [Next step].
// Entry condition B: docType=M, mode=info, no next step -> empty action area.

describe('T866-S4 — D030 §4 #4: M → [다음단계] or info(empty)', () => {
  it('#4a: M + mode=next + nextStepLabel=DS → [Next step DS] 버튼, Approve/Reject 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0000-M',
        docType: 'M',
        reviewStatus: null,
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'DS',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('DS')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('#4b: M + mode=info (다음 단계 없음) → .sfb-actions 존재하나 버튼 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0000-M',
        docType: 'M',
        reviewStatus: null,
        mode: 'info',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-actions').exists()).toBe(true)
    expect(wrapper.find('.sfb-actions button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── T866-S5 ── D030 section 4 #5/#6: answered Q with next path -> [Next step], otherwise Q guidance ──
// Entry condition A: docType=Q, mode=next -> Q answered and a next path exists.
// Entry condition B: docType=Q, mode=q -> unanswered or no next path -> Q guidance text.

describe('T866-S5 — D030 §4 #5/#6: Q → [다음단계] or Q 안내', () => {
  it('#5: Q + mode=next (answered+next) → [Next step] 버튼, Approve/Reject 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.q.0001-Q',
        docType: 'Q',
        reviewStatus: null,
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'D',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('D')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('#6: Q + mode=q (미답변/no next) → .sfb-hint 표시, 액션 버튼 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.q.0001-Q',
        docType: 'Q',
        reviewStatus: null,
        mode: 'q',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-hint').exists()).toBe(true)
    expect(wrapper.find('.sfb-actions button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})

// ── T866-S6 ── D030 section 4 #7: approved DS/D/T/TS/.../L -> [Next step] or completed ──
// Entry condition A: docType=DS, reviewStatus=approved, mode=next, nextStep exists.
// Entry condition B: docType=TS, reviewStatus=approved, mode=info, no nextStep.
// Expected action A: enabled [Next step] button; no Approve or Reject.
// Expected action B: .sfb-actions exists with no buttons (completed state).

describe('T866-S6 — D030 §4 #7: 산출물 approved → [다음단계] or info', () => {
  it('#7a: DS + approved + mode=next + nextStepLabel=D → [Next step D] 활성, Approve/Reject 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0002-DS',
        docType: 'DS',
        reviewStatus: 'approved',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'D',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.text()).toContain('D')
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('#7b: TS + approved + mode=info (다음 단계 없음) → .sfb-actions 존재, 버튼 없음', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0004-TS',
        docType: 'TS',
        reviewStatus: 'approved',
        mode: 'info',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-actions').exists()).toBe(true)
    expect(wrapper.find('.sfb-actions button').exists()).toBe(false)
    // Use a button selector because the status badge may also contain "Approved".
    expect(wrapper.find('button.btn-success').exists()).toBe(false)
    expect(wrapper.find('button.btn-danger').exists()).toBe(false)
  })
})

// ── T866-S7 ── D030 section 4 #8: rejected deliverable -> rejection rework toolbar ──
// Entry condition: docType=D, reviewStatus=rejected, mode=rejected.
// Expected actions: rework toolbar (.sfb-actions--rework) with Copy Mention, Run Command, and Revision Complete.
// No standard Approve or Reject.

describe('T866-S7 — D030 §4 #8: 산출물 rejected → 반려 재작업 툴바', () => {
  it('D + rejected + mode=rejected → .sfb-actions--rework, 버튼 ≥3, Approve/Reject 없음', () => {
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
    const reworkBtns = wrapper.findAll('.sfb-actions--rework button')
    expect(reworkBtns.length).toBeGreaterThanOrEqual(3)
    expect(wrapper.text()).not.toContain('Approve')
    // The standard Reject button (btn-danger) must also be absent.
    expect(wrapper.find('button.btn-danger').exists()).toBe(false)
  })
})

// ── T866-S8 ── D030 section 4 #9: pending/revised deliverable -> [Approve], [Reject], [Request review] ──
// Entry condition A: docType=T, reviewStatus=pending_review, mode=review.
// Entry condition B: docType=NR, reviewStatus=revised, mode=review.
// Expected actions: [Approve] and [Reject], plus the split button when a non-R document can request review.

describe('T866-S8 — D030 §4 #9: 산출물 pending_review/revised → [승인][반려][검수요청]', () => {
  it('#9a: T + pending_review + mode=review → Approve + Reject 버튼 표시', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0003-T',
        docType: 'T',
        reviewStatus: 'pending_review',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(false)
  })

  it('#9b: NR + revised + mode=review → Approve + Reject 버튼 표시', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...BASE_PROPS,
        docId: 'proj.grp.0001.0004-NR',
        docType: 'NR',
        reviewStatus: 'revised',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(false)
  })

  it('#9c: non-R + pending_review + mode=review → 검수요청 split 버튼(.ab-split-wrap) 포함', () => {
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
    expect(wrapper.find('.ab-split-wrap').exists()).toBe(true)
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })
})
