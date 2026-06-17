// T862 — headDocId null-coalesce trap guard
// Memory guard: [feedback_head_viewed_nullcoalesce_trap]
// Rule: headDocId が NULL であっても headXxx props はそのまま NULL のまま。
//       ?? viewedXxx へのフォールバックは絶対に行わない。

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
} from '@main/workflow/workflowViewState'

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

// ── Shared base input for workflowViewState tests ─────────────────────────────

const baseInput: WorkflowViewInput = {
  tabTypeCode: 'D',
  tabReviewStatus: 'pending_review',
  workflowSteps: ['DS', 'D', 'T'],
  headType: null,
  headStatus: null,
  headDocId: null,
  headDocReviewStatus: null,
  nextStepExists: false,
  qStatus: null,
}

// ── Shared props for ReviewActionBar tests ────────────────────────────────────

const barBase = {
  docId: 'test.p.0001.0003-D',
  projectId: 'test-p',
  groupId: 'test-g',
  docRef: 'REF-003',
  reviewStatus: 'pending_review' as string | null,
}

// ─────────────────────────────────────────────────────────────────────────────

describe('T862 headDocId null-coalesce trap', () => {

  // ── S1: workflowViewState — headDocId pass-through / null isolation ────────

  describe('S1 — resolveWorkflowViewState headDocId isolation', () => {
    it('S1-a: headDocId=null input → output headDocId is strictly null', () => {
      const result = resolveWorkflowViewState({
        ...baseInput,
        headDocId: null,
      })
      expect(result.headDocId).toBeNull()
    })

    it('S1-b: headDocId=undefined input → output headDocId is null (never viewedDoc id)', () => {
      const result = resolveWorkflowViewState({
        ...baseInput,
        headDocId: undefined,
      })
      expect(result.headDocId).toBeNull()
    })

    it('S1-c: headDocId set to real id → output headDocId equals that id exactly', () => {
      const result = resolveWorkflowViewState({
        ...baseInput,
        headDocId: 'test.p.0001.0005-D',
        headType: 'D',
        headStatus: 'pending',
        tabReviewStatus: 'approved',
        nextStepExists: true,
      })
      expect(result.headDocId).toBe('test.p.0001.0005-D')
    })
  })

  // ── S2: ReviewActionBar — showHeadLabel gate when headDocId=null ─────────

  describe('S2 — showHeadLabel=false when headDocId=null', () => {
    it('S2-a: headDocId=null → head-label block not rendered; docRef rendered instead', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...barBase,
          headDocId: null,
          viewedDocId: 'test.p.0001.0003-D',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      // docRef must be visible (normal label branch)
      expect(wrapper.text()).toContain('REF-003')

      // head section renders headDocId text (null → nothing from headDocId span)
      // Verify by ensuring the sfb-mono span content is the docRef, not some
      // viewed-doc substitution. The component shows docRef in the v-else branch.
      const monoSpan = wrapper.find('.sfb-mono')
      expect(monoSpan.text()).toBe('REF-003')
    })

    it('S2-b: headDocId=null + mode=next → sfb-mono shows docRef (not viewedDocId value)', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...barBase,
          headDocId: null,
          viewedDocId: 'test.p.0001.0003-D',
          mode: 'next',
          nextStepLabel: 'T',
          canNextAction: true,
        },
        global: { plugins: [i18n] },
      })

      const monoSpan = wrapper.find('.sfb-mono')
      expect(monoSpan.text()).toBe('REF-003')
    })
  })

  // ── S3: ReviewActionBar — isViewingPastDoc=false when headDocId=null ───────

  describe('S3 — isViewingPastDoc never true when headDocId=null', () => {
    it('S3-a: headDocId=null, viewedDocId set → no navigation button rendered', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...barBase,
          headDocId: null,
          viewedDocId: 'test.p.0001.0003-D',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('로 이동')
    })

    it('S3-b: headDocId=null, viewedDocId differs from any real doc → still no nav button', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...barBase,
          headDocId: null,
          viewedDocId: 'some.other.0001.0009-DS',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('로 이동')
      // Standard actions must remain visible
      expect(wrapper.text()).toContain('Approve')
    })
  })

  // ── S4: ReviewActionBar — headDocTitle/headDocLabel ignored when headDocId=null ──

  describe('S4 — headDocTitle and headDocLabel not exposed when headDocId=null', () => {
    it('S4-a: headDocId=null + headDocTitle/headDocLabel non-null → neither appears in DOM', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...barBase,
          headDocId: null,
          headDocTitle: 'Should Not Appear Title',
          headDocLabel: 'D',
          viewedDocId: 'test.p.0001.0003-D',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('Should Not Appear Title')
      // headDocLabel drives headTypeLabel; with headDocId=null the head-label block is hidden
      // so headTypeLabel text must not appear in the rendered output via that block
      expect(wrapper.html()).not.toContain('sfb-title') // head-label <span class="sfb-title"> not rendered
    })
  })

})
