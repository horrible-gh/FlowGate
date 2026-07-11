import { describe, expect, it } from 'vitest'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
  type WorkflowViewState,
  type StepState,
} from '../../src/main/workflow/workflowViewState'

// Helper: construct full StepState with canonical className/iconClass
function ss(code: string, visual: 'done' | 'highlight' | 'rejected' | 'current' | 'future') {
  const CLS: Record<string, string> = {
    done: 'done',
    highlight: 'wf-next-action dip-step-clickable dip-step-active',
    rejected: 'wf-rejected dip-step-rejected',
    current: 'current',
    future: 'future dip-step-disabled',
  }
  const ICN: Record<string, string> = {
    done: 'check-circle',
    highlight: 'circle',
    rejected: 'x-circle',
    current: 'radio-button',
    future: 'circle',
  }
  return { code, visual, className: CLS[visual], iconClass: ICN[visual] }
}
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

function s(overrides: Partial<WorkflowViewInput>): WorkflowViewState {
  return resolveWorkflowViewState({ ...baseInput, ...overrides })
}

describe('resolveWorkflowViewState', () => {

  // ── D031 §4.4 — R undecided ───────────────────────────────────────────────

  it('R undecided (null reviewStatus) → mode=workflow, no highlight, no next', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: null })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    expect(result.nextStepActive).toBe(false)
  })

  it('R undecided (non-wf_ reviewStatus) → mode=workflow', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: 'pending_review' })
    expect(result.mode).toBe('workflow')
    expect(result.canNextAction).toBe(false)
  })

  // ── R0001 / NR0003 — decided judged by authority signal, not stale prefix ──

  it('R with stale (pre-decision) reviewStatus but materialized head → NOT mode=workflow', () => {
    // Regression: a tab that missed the live SSE keeps a pre-decision doc_review_status
    // (here 'approved'), yet the server already materialized the workflow head. The
    // decide gate must treat the present headType as authority and NOT revive the
    // [워크플로 결정] button (which caused the 409 already_decided burst in R0001).
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'approved',
      workflowSteps: ['R', 'N', 'NR', 'AC'],
      headType: 'N',
      headStatus: 'pending',
    })
    expect(result.mode).not.toBe('workflow')
    expect(result.mode).toBe('next')
  })

  it('R with null reviewStatus but headType present → decided (mode=next), headType alone suffices', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: null,
      headType: 'T',
      headStatus: 'pending',
    })
    expect(result.mode).toBe('next')
  })

  it('R genuinely undecided (no headType) still → mode=workflow', () => {
    // The cross-check must not over-fire: with no head the decide button stays — even
    // when the strip is fed sequence cells for an undecided R (v2-1 contract).
    expect(s({ tabTypeCode: 'R', tabReviewStatus: 'pending_review' }).mode).toBe('workflow')
    expect(s({ tabTypeCode: 'R', tabReviewStatus: null, workflowSteps: ['R', 'T', 'AC'] }).mode).toBe('workflow')
  })

  // ── D031 §4.4 — R decided + head=pending ─────────────────────────────────

  it('R decided + head pending → mode=next, canNextAction=true, highlight=headType', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'DS',
      headStatus: 'pending',
      headDocId: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.currentStepCode).toBe('R')
    expect(result.highlightStepCode).toBe('DS')
    expect(result.nextStepCode).toBe('DS')
    expect(result.nextStepActive).toBe(true)
  })

  it('R decided + headStatus=null treated as pending → mode=next, canNextAction=true', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'D',
      headStatus: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('D')
  })

  // ── D031 §4.4 — R decided + head=in_progress ─────────────────────────────

  it('R decided + head in_progress → mode=next (PM: R branch preserved), canNextAction=false, highlight=headType', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headType: 'T',
      headStatus: 'in_progress',
      headDocId: 'doc-42',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBe('T')
    expect(result.nextStepCode).toBe('T')
    expect(result.nextStepActive).toBe(true)
    expect(result.headDocId).toBe('doc-42')
  })

  // ── D031 §4.4 — R decided + wf_done ─────────────────────────────────────

  it('R decided + wf_done → mode=info, no highlight, no next', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: 'wf_done' })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    expect(result.nextStepActive).toBe(false)
  })

  // ── D031 §4.4 — non-R tab approved + next step exists ────────────────────

  it.each(['DS', 'D', 'T', 'TS', 'N', 'NR', 'TR', 'TSR', 'P', 'DB', 'L'])(
    'non-R %s approved + nextStepExists → mode=next, highlight=headType',
    (tabTypeCode) => {
      const result = s({
        tabTypeCode,
        tabReviewStatus: 'approved',
        nextStepExists: true,
        headType: 'T',
        headDocId: 'doc-10',
      })
      expect(result.mode).toBe('next')
      expect(result.canNextAction).toBe(true)
      expect(result.highlightStepCode).toBe('T')
      expect(result.nextStepActive).toBe(true)
      expect(result.headDocId).toBe('doc-10')
    },
  )

  // ── D031 §4.4 — non-R tab approved + no next step ────────────────────────

  it.each(['DS', 'D', 'T', 'TR', 'P', 'L'])(
    'non-R %s approved + nextStepExists=false → mode=info',
    (tabTypeCode) => {
      const result = s({ tabTypeCode, tabReviewStatus: 'approved', nextStepExists: false })
      expect(result.mode).toBe('info')
      expect(result.canNextAction).toBe(false)
      expect(result.highlightStepCode).toBeNull()
    },
  )

  // ── D031 §4.4 — non-R tab pending_review ─────────────────────────────────

  it.each(['DS', 'D', 'T', 'TR', 'NR', 'P', 'L', 'DB'])(
    'non-R %s pending_review → mode=review, no highlight',
    (tabTypeCode) => {
      const result = s({ tabTypeCode, tabReviewStatus: 'pending_review', nextStepExists: true })
      expect(result.mode).toBe('review')
      expect(result.canNextAction).toBe(false)
      expect(result.highlightStepCode).toBeNull()
    },
  )

  it('revised reviewStatus → mode=review', () => {
    const result = s({ tabTypeCode: 'D', tabReviewStatus: 'revised', nextStepExists: true })
    expect(result.mode).toBe('review')
  })

  // ── D031 §4.4 — non-R tab rejected ───────────────────────────────────────

  it.each(['DS', 'D', 'T', 'TR', 'P', 'L', 'DB'])(
    'non-R %s rejected → mode=rejected, no highlight',
    (tabTypeCode) => {
      const result = s({ tabTypeCode, tabReviewStatus: 'rejected' })
      expect(result.mode).toBe('rejected')
      expect(result.canNextAction).toBe(false)
      expect(result.highlightStepCode).toBeNull()
    },
  )

  // ── D031 §4.4 — Q tab ────────────────────────────────────────────────────

  it('Q answered + nextStepExists → mode=next (D030 §4 #5)', () => {
    const result = s({
      tabTypeCode: 'Q',
      qStatus: 'done',
      nextStepExists: true,
      headType: 'DS',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('DS')
  })

  it('Q answered + nextStepExists=false → mode=q (D030 §4 #6)', () => {
    const result = s({ tabTypeCode: 'Q', qStatus: 'done', nextStepExists: false })
    expect(result.mode).toBe('q')
    expect(result.canNextAction).toBe(false)
  })

  it('Q unanswered → mode=q regardless of nextStepExists (D030 §4 #6)', () => {
    expect(s({ tabTypeCode: 'Q', qStatus: 'pending', nextStepExists: true }).mode).toBe('q')
    expect(s({ tabTypeCode: 'Q', qStatus: null, nextStepExists: true }).mode).toBe('q')
  })

  // ── D031 §4.4 — M tab ────────────────────────────────────────────────────

  it('M + nextStepExists → mode=next (D030 §4 #4 [Next step])', () => {
    const result = s({ tabTypeCode: 'M', nextStepExists: true, headType: 'DS' })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('DS')
  })

  it('M + nextStepExists=false → mode=info (D030 §4 #4 [Complete])', () => {
    const result = s({ tabTypeCode: 'M', nextStepExists: false })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
  })

  // ── CH (conversation) tab — TR0044.0010 rev3 ──────────────────────────────
  // rev3 reverses rev2's special 'conversation' mode: a CH doc is a normal workflow
  // node, so its action bar (next-action button) is restored. The "no [다음 단계 진행]/
  // [빈 문서 생성] for a conversation" rule moved to the creation edge — ReviewActionBar
  // shows a single [대화 문서 생성] when the NEXT step is CH (nextStepCode === 'CH').

  it('CH approved with a next step → mode=next (action bar restored)', () => {
    const result = s({
      tabTypeCode: 'CH',
      tabReviewStatus: 'approved',
      nextStepExists: true,
      headType: 'TR',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
  })

  it('CH approved with no next step → mode=info (no special conversation mode)', () => {
    const result = s({ tabTypeCode: 'CH', tabReviewStatus: 'approved', nextStepExists: false })
    expect(result.mode).toBe('info')
  })

  // ── Default fallback ──────────────────────────────────────────────────────

  it('unknown type code with no reviewStatus → mode=review (default)', () => {
    const result = s({ tabTypeCode: 'UNKNOWN', tabReviewStatus: null })
    expect(result.mode).toBe('review')
    expect(result.canNextAction).toBe(false)
  })

  // ── sequence-complete guard (non-R headStatus=done) ───────────────────────

  it('non-R with headStatus=done → mode=sequence-complete (entire sequence finished)', () => {
    const result = s({ tabTypeCode: 'D', headStatus: 'done', tabReviewStatus: 'approved' })
    expect(result.mode).toBe('sequence-complete')
    expect(result.canNextAction).toBe(false)
  })

  // ── 0119 B0001 / NR0009 §6.2/§6.3: decided-but-empty workflow recovery ─────
  // Server reports headStatus='empty' when a decided workflow's sequence row exists but
  // has zero items (every step deleted). It must route to mode='workflow-recover' on EVERY
  // tab — never the phantom mode='next' (R/B) nor mode='sequence-complete' ([완료]) (non-R).

  it('R decided + headStatus=empty → mode=workflow-recover (NOT phantom next), canNextAction=false', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      headStatus: 'empty',
      headType: null,
      workflowSteps: [],
    })
    expect(result.mode).toBe('workflow-recover')
    expect(result.mode).not.toBe('next')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepCode).toBeNull()
    expect(result.nextStepActive).toBe(false)
  })

  it('B decided + headStatus=empty → mode=workflow-recover (bug-root parity)', () => {
    const result = s({
      tabTypeCode: 'B',
      tabReviewStatus: 'wf_in_progress',
      headStatus: 'empty',
      headType: null,
      workflowSteps: [],
    })
    expect(result.mode).toBe('workflow-recover')
    expect(result.canNextAction).toBe(false)
  })

  it('non-R sibling tab + headStatus=empty → mode=workflow-recover (NOT sequence-complete/[완료])', () => {
    const result = s({
      tabTypeCode: 'D',
      tabReviewStatus: 'approved',
      headStatus: 'empty',
      headType: null,
      workflowSteps: [],
    })
    expect(result.mode).toBe('workflow-recover')
    expect(result.mode).not.toBe('sequence-complete')
    expect(result.canNextAction).toBe(false)
  })

  it('M tab + headStatus=empty → mode=workflow-recover (guard precedes M branch)', () => {
    const result = s({
      tabTypeCode: 'M',
      headStatus: 'empty',
      headType: null,
      nextStepExists: true,
      workflowSteps: [],
    })
    expect(result.mode).toBe('workflow-recover')
    expect(result.canNextAction).toBe(false)
  })

  // ── D030 §4 #7 — multi-activation (D/P/L/DB + V) preservation ────────────

  it('non-R approved + headType=D → highlightDesignSeries=true (multi-activation preserved)', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      nextStepExists: true,
      headType: 'D',
    })
    expect(result.mode).toBe('next')
    expect(result.highlightStepCode).toBe('D')
    expect(result.highlightDesignSeries).toBe(true)
  })

  it('non-R approved + headType=P → highlightDesignSeries=true', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      nextStepExists: true,
      headType: 'P',
    })
    expect(result.highlightDesignSeries).toBe(true)
  })

  it('non-R approved + headType=T (not design-series) → highlightDesignSeries=false', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      nextStepExists: true,
      headType: 'T',
    })
    expect(result.highlightDesignSeries).toBe(false)
  })

  // ── Output field consistency ──────────────────────────────────────────────

  it('currentStepCode mirrors tabTypeCode in all cases', () => {
    expect(s({ tabTypeCode: 'D', tabReviewStatus: 'approved', nextStepExists: true }).currentStepCode).toBe('D')
    expect(s({ tabTypeCode: 'R', tabReviewStatus: null }).currentStepCode).toBe('R')
    expect(s({ tabTypeCode: 'Q', qStatus: 'pending' }).currentStepCode).toBe('Q')
  })

  it('headDocId propagates to output when set', () => {
    const result = s({
      tabTypeCode: 'D',
      tabReviewStatus: 'approved',
      nextStepExists: true,
      headType: 'TR',
      headDocId: 'doc-99',
    })
    expect(result.headDocId).toBe('doc-99')
    expect(result.headDocLabel).toBe('TR')
  })
})

// ── D031 §4.4 v2 — stepStates + nextStepIndex regression cases ────────────────

const steps6 = ['R', 'M', 'DS', 'D', 'T', 'TR']
const allFuture6: StepState[] = steps6.map(code => ss(code, 'future'))
const allDone6: StepState[] = steps6.map(code => ss(code, 'done'))

describe('resolveWorkflowViewState — stepStates v2 regression', () => {

  // v2-1: R undecided
  it('v2-1: R undecided → all stepStates future, nextStepIndex=null, mode=workflow', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: null, workflowSteps: steps6 })
    expect(result.mode).toBe('workflow')
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual(allFuture6)
  })

  // v2-2: R decided + head=DS pending
  it('v2-2: R decided + head=DS pending → done×2 / highlight(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'pending',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-3: R decided + head=DS in_progress + rejected (NR157 case)
  it('v2-3 (NR157): R + head=DS in_progress + rejected → mode=rejected, done×2 / rejected(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocId: 'doc-ds-1',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'rejected'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-4: R decided + head=DS in_progress (not rejected)
  it('v2-4: R + head=DS in_progress (not rejected) → mode=next, canNextAction=false, done×2 / highlight(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'pending_review',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-5: R wf_done
  it('v2-5: R wf_done → all done, nextStepIndex=null, mode=info', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: 'wf_done', workflowSteps: steps6 })
    expect(result.mode).toBe('info')
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual(allDone6)
  })

  // v2-6: Non-R pending_review (DS tab, head=DS in_progress)
  it('v2-6: non-R DS pending_review, head=DS in_progress → mode=review, done×2 / highlight(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'pending_review',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'pending_review',
    })
    expect(result.mode).toBe('review')
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-7: Non-R rejected (DS tab, DS rejected)
  it('v2-7: non-R DS rejected → mode=rejected, done×2 / rejected(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'rejected',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'rejected'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-8: Q tab — stepStates reflects sequence; Q is not in workflowSteps
  it('v2-8: Q unanswered, head=DS pending → mode=q, stepStates reflects sequence, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'Q',
      qStatus: null,
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'pending',
    })
    expect(result.mode).toBe('q')
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  it('v2-8b: Q tab, head not in workflowSteps (Q outside sequence) → nextStepIndex=null, all future', () => {
    const result = s({ tabTypeCode: 'Q', qStatus: null, workflowSteps: steps6, headType: null })
    expect(result.mode).toBe('q')
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual(allFuture6)
  })

  // v2-9: M tab after auto-done → M cell = done, DS cell = highlight
  it('v2-9: M tab + nextStepExists, head=DS pending → mode=next, M=done, DS=highlight, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps6,
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates[1]).toEqual(ss('M', 'done'))
    expect(result.stepStates[2]).toEqual(ss('DS', 'current'))
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // v2-10/11/12: NR158 non-R partial-progress regression cases
  const steps4 = ['R', 'DS', 'D', 'AC']

  // v2-10: R tab — R decided + DS approved + D not created (anchors R-tab path, already works)
  it('v2-10 (NR158): R tab, R decided + DS approved + D not created → mode=next, done×2 / highlight(D) / future(AC), nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps4,
      headType: 'D',
      headStatus: 'pending',
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('DS', 'done'),
      ss('D', 'current'),
      ss('AC', 'future'),
    ])
  })

  // v2-11: DS tab — same scenario, post-BE-fix inputs (demonstrates NR158 bug is closed)
  it('v2-11 (NR158): DS tab, DS approved + D not created, headType=D headStatus=pending → mode=next, done×2 / highlight(D) / future(AC), nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      workflowSteps: steps4,
      headType: 'D',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('DS', 'done'),
      ss('D', 'current'),
      ss('AC', 'future'),
    ])
  })

  // v2-12: DS tab — truly complete (sequence-complete guard must still fire correctly)
  it('v2-12 (NR158): DS tab, truly complete (headStatus=done, nextStepExists=false) → mode=sequence-complete, all done, nextStepIndex=null', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'approved',
      workflowSteps: steps4,
      headType: null,
      headStatus: 'done',
      nextStepExists: false,
    })
    expect(result.mode).toBe('sequence-complete')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('AC', 'done'),
    ])
  })
})

// ── T855 — M-tab partial-progress scenarios ───────────────────────────────────────────────────────

describe('resolveWorkflowViewState — M-tab partial-progress (T855)', () => {
  const steps6m = ['R', 'M', 'DS', 'D', 'T', 'TR']

  // T855-S1: M partial-progress — head=DS in_progress, no headStatus guard in M branch
  // Unlike R tab (which sets canNextAction=false when head=in_progress), M branch always
  // returns nextAction() when nextStepExists=true, so canNextAction=true regardless of headStatus.
  it('T855-S1: M partial-progress, head=DS in_progress → mode=next, canNextAction=true (M branch carries no headStatus guard)', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps6m,
      headType: 'DS',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'current'),
      ss('D', 'future'),
      ss('T', 'future'),
      ss('TR', 'future'),
    ])
  })

  // T855-S2: M + tabReviewStatus=pending_review — M branch fires before reviewStatus path
  // [feedback_memo_no_review_actions]: M tab never produces mode='review'.
  it('T855-S2: M + tabReviewStatus=pending_review + nextStepExists → mode=next, not review ([feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      tabReviewStatus: 'pending_review',
      workflowSteps: steps6m,
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.mode).not.toBe('review')
    expect(result.canNextAction).toBe(true)
  })

  // T855-S3: M + tabReviewStatus=rejected — M branch fires before reviewStatus path
  // [feedback_memo_no_review_actions]: M tab never produces mode='rejected'.
  it('T855-S3: M + tabReviewStatus=rejected + nextStepExists → mode=next, not rejected ([feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      tabReviewStatus: 'rejected',
      workflowSteps: steps6m,
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.mode).not.toBe('rejected')
    expect(result.canNextAction).toBe(true)
  })

  // T855-S4: M + headStatus=done — sequence-complete guard fires before M branch
  // [feedback_actionbar_always_shows]: mode is never null; sequence-complete is a valid non-null mode.
  it('T855-S4: M + headStatus=done → mode=sequence-complete (guard intercepts before M branch, [feedback_actionbar_always_shows])', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps6m,
      headType: null,
      headStatus: 'done',
      nextStepExists: false,
    })
    expect(result.mode).toBe('sequence-complete')
    expect(result.canNextAction).toBe(false)
    expect(result.mode).not.toBeNull()
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('T', 'done'),
      ss('TR', 'done'),
    ])
  })
})

// ── T856 — T-tab partial-progress scenarios ───────────────────────────────────────────────────────

describe('resolveWorkflowViewState — T-tab partial-progress (T856)', () => {
  const steps6t = ['R', 'M', 'DS', 'D', 'T', 'TR']

  // T856-S1: T tab, T submitted (pending_review) — partial-progress: T is the head in review.
  // reviewStatus='pending_review' → noAction('review'); normalSS headType=T → T=highlight.
  it('T856-S1: T partial-progress, T pending_review, head=T in_progress → mode=review, done×4 / highlight(T) / future(TR), nextStepIndex=4', () => {
    const result = s({
      tabTypeCode: 'T',
      tabReviewStatus: 'pending_review',
      workflowSteps: steps6t,
      headType: 'T',
      headStatus: 'in_progress',
      headDocReviewStatus: 'pending_review',
    })
    expect(result.mode).toBe('review')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepIndex).toBe(4)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('T', 'current'),
      ss('TR', 'future'),
    ])
  })

  // T856-S2: T tab, T approved + TR not yet created (NR158-equivalent for T).
  // reviewStatus='approved' + nextStepExists=true → nextAction(); headType=TR pending → TR=highlight.
  it('T856-S2: T approved + TR not created, headType=TR headStatus=pending → mode=next, done×5 / highlight(TR), nextStepIndex=5', () => {
    const result = s({
      tabTypeCode: 'T',
      tabReviewStatus: 'approved',
      workflowSteps: steps6t,
      headType: 'TR',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(5)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('T', 'done'),
      ss('TR', 'current'),
    ])
  })

  // T856-S3: T tab, T approved + no next step (T is terminal step in sequence).
  // reviewStatus='approved' + nextStepExists=false → noAction('info').
  // [feedback_actionbar_always_shows]: mode is non-null even with no forward action.
  it('T856-S3: T approved + no nextStep → mode=info, canNextAction=false, mode not null ([feedback_actionbar_always_shows])', () => {
    const steps5t = ['R', 'M', 'DS', 'D', 'T']
    const result = s({
      tabTypeCode: 'T',
      tabReviewStatus: 'approved',
      workflowSteps: steps5t,
      headType: 'T',
      headStatus: 'in_progress',
      nextStepExists: false,
    })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
    expect(result.mode).not.toBeNull()
    expect(result.nextStepIndex).toBe(4)
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('T', 'current'),
    ])
  })

  // T856-S4: T tab + headStatus=done — sequence-complete guard fires before reviewStatus path.
  // [feedback_actionbar_always_shows]: mode='sequence-complete' is a valid non-null mode.
  it('T856-S4: T + headStatus=done → mode=sequence-complete, all done, nextStepIndex=null ([feedback_actionbar_always_shows])', () => {
    const result = s({
      tabTypeCode: 'T',
      tabReviewStatus: 'approved',
      workflowSteps: steps6t,
      headType: null,
      headStatus: 'done',
      nextStepExists: false,
    })
    expect(result.mode).toBe('sequence-complete')
    expect(result.canNextAction).toBe(false)
    expect(result.mode).not.toBeNull()
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual([
      ss('R', 'done'),
      ss('M', 'done'),
      ss('DS', 'done'),
      ss('D', 'done'),
      ss('T', 'done'),
      ss('TR', 'done'),
    ])
  })
})

// ── T860 — pending head type from server (commit 72bb39a) ────────────────────────────────────────
//
// Commit 72bb39a added workflow_head_type to the pending branch of _parse_doc_workflow.
// These scenarios verify the client correctly reflects the server-supplied headType when
// headStatus='pending' (i.e. head doc not yet created, first unrealized seq step).
// Each scenario mirrors one of the four Python server tests added in that commit.

describe('resolveWorkflowViewState — pending head type from server (T860)', () => {
  const steps5m = ['M', 'DS', 'D', 'T', 'TR']

  // T860-S1: mirrors server test_head_type_filled_for_pending_with_unrealized_step
  // Server: seq=['M','DS','D','T','TR'], M approved → headType='DS', headStatus='pending', headDocId=null.
  // Client must: mode='next', canNextAction=true, highlightStepCode='DS', headDocId=null.
  // stepStates: M=done (before DS), DS=highlight, D/T/TR=future.
  it('T860-S1: R decided, M approved → server headType=DS headStatus=pending → mode=next, canNextAction=true, DS highlighted, headDocId=null', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps5m,
      headType: 'DS',
      headStatus: 'pending',
      headDocId: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('DS')
    expect(result.nextStepCode).toBe('DS')
    expect(result.headDocId).toBeNull()
    expect(result.nextStepIndex).toBe(1)
    expect(result.stepStates).toEqual([
      ss('M',  'done'),
      ss('DS', 'current'),
      ss('D',  'future'),
      ss('T',  'future'),
      ss('TR', 'future'),
    ])
  })

  // T860-S2: mirrors server test_head_type_skips_m_in_sequence_when_picking_first_unrealized
  // Server: seq=['M','DS'], M is NON_HEAD_TYPE → skipped; DS is first actionable → headType='DS'.
  // Client sees M before DS in workflowSteps; M appears as done (index < headIndex), DS=highlight.
  it('T860-S2: M is NON_HEAD_TYPE (skipped by server), first actionable=DS → DS highlighted, M shown as done', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: ['M', 'DS'],
      headType: 'DS',
      headStatus: 'pending',
      headDocId: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('DS')
    expect(result.nextStepIndex).toBe(1)
    expect(result.stepStates).toEqual([
      ss('M',  'done'),
      ss('DS', 'current'),
    ])
  })

  // T860-S3: mirrors server test_head_done_when_only_m_in_sequence_approved
  // Server: seq=['M'], M approved → no actionable step remains → headStatus='done', headType=null.
  // For R tab still showing wf_in_progress: R branch fires; headPending=false → canNextAction=false,
  // highlightStepCode=null (headType=null → headIndex=-1 → all steps future in normalSS).
  it('T860-S3: only M in seq approved → server sends headStatus=done headType=null → R tab canNextAction=false, no highlight', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: ['M'],
      headType: null,
      headStatus: 'done',
      headDocId: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    expect(result.headDocId).toBeNull()
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual([ss('M', 'future')])
  })

  // T860-S4: mirrors server test_head_type_first_design_step_when_no_m_in_sequence
  // Server: seq=['D','T'], neither created (no M at all) → headType='D', headStatus='pending', headDocId=null.
  // Client must: D=highlight, T=future, nextStepIndex=0, canNextAction=true.
  it('T860-S4: no M in seq, first step D is pending head → D highlighted, T future, headDocId=null', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: ['D', 'T'],
      headType: 'D',
      headStatus: 'pending',
      headDocId: null,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('D')
    expect(result.nextStepCode).toBe('D')
    expect(result.headDocId).toBeNull()
    expect(result.nextStepIndex).toBe(0)
    expect(result.stepStates).toEqual([
      ss('D', 'current'),
      ss('T', 'future'),
    ])
  })
})

// ── T858 — cross-type partial-progress consistency (stepStates matrix) ────────────────────────────

describe('resolveWorkflowViewState — cross-type partial-progress consistency (T858)', () => {
  const steps5 = ['M', 'DS', 'D', 'T', 'TR']

  // T858-S1: step 2/5 in_progress — all tabs emit identical normalSS.
  // Regression guard: any tab branch accidentally switching to allFutureSS or allDoneSS diverges here.
  const step2SS = [
    ss('M',  'done'),
    ss('DS', 'current'),
    ss('D',  'future'),
    ss('T',  'future'),
    ss('TR', 'future'),
  ]

  it.each<[string, Partial<WorkflowViewInput>]>([
    ['R',               { tabTypeCode: 'R',  tabReviewStatus: 'wf_in_progress', headType: 'DS', headStatus: 'in_progress', headDocReviewStatus: 'pending_review' }],
    ['M',               { tabTypeCode: 'M',  headType: 'DS', headStatus: 'in_progress', nextStepExists: true }],
    ['Q (unanswered)',  { tabTypeCode: 'Q',  qStatus: null,  headType: 'DS', headStatus: 'in_progress', headDocReviewStatus: 'pending_review' }],
    ['DS',              { tabTypeCode: 'DS', tabReviewStatus: 'pending_review', headType: 'DS', headStatus: 'in_progress', headDocReviewStatus: 'pending_review' }],
    ['D (not created)', { tabTypeCode: 'D',  tabReviewStatus: null, headType: 'DS', headStatus: 'in_progress', headDocReviewStatus: 'pending_review' }],
  ])(
    'T858-S1: step 2/5 in_progress — %s tab → [done(M), highlight(DS), future×3], nextStepIndex=1',
    (_, overrides) => {
      const result = s({ ...overrides, workflowSteps: steps5 })
      expect(result.stepStates).toEqual(step2SS)
      expect(result.nextStepIndex).toBe(1)
    },
  )

  // T858-S2: step 3/5 pending — all tabs emit identical normalSS.
  // headType=D pending: DS is already done, D is the new head (not yet created as a doc).
  const step3SS = [
    ss('M',  'done'),
    ss('DS', 'done'),
    ss('D',  'current'),
    ss('T',  'future'),
    ss('TR', 'future'),
  ]

  it.each<[string, Partial<WorkflowViewInput>]>([
    ['R',                { tabTypeCode: 'R',  tabReviewStatus: 'wf_in_progress', headType: 'D', headStatus: 'pending' }],
    ['M',                { tabTypeCode: 'M',  headType: 'D', headStatus: 'pending', nextStepExists: true }],
    ['DS (approved)',    { tabTypeCode: 'DS', tabReviewStatus: 'approved', headType: 'D', headStatus: 'pending', nextStepExists: true }],
    ['D (just created)', { tabTypeCode: 'D',  tabReviewStatus: 'pending_review', headType: 'D', headStatus: 'pending' }],
  ])(
    'T858-S2: step 3/5 pending — %s tab → [done(M), done(DS), highlight(D), future×2], nextStepIndex=2',
    (_, overrides) => {
      const result = s({ ...overrides, workflowSteps: steps5 })
      expect(result.stepStates).toEqual(step3SS)
      expect(result.nextStepIndex).toBe(2)
    },
  )

  // T858-S3: rejected head — R tab (NR157 branch) and DS tab (reviewStatus path) both emit
  // rejected visual at the same step position. normalSS is the shared source for both paths.
  const step2RejectedSS = [
    ss('M',  'done'),
    ss('DS', 'rejected'),
    ss('D',  'future'),
    ss('T',  'future'),
    ss('TR', 'future'),
  ]

  it('T858-S3a: R tab + head=DS rejected (NR157) → mode=rejected, [done(M), rejected(DS), future×3], nextStepIndex=1', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps5,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.stepStates).toEqual(step2RejectedSS)
    expect(result.nextStepIndex).toBe(1)
  })

  it('T858-S3b: DS tab + tabReviewStatus=rejected, head=DS rejected → mode=rejected, stepStates identical to R tab (cross-type rejected visual consistency)', () => {
    const result = s({
      tabTypeCode: 'DS',
      tabReviewStatus: 'rejected',
      workflowSteps: steps5,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.stepStates).toEqual(step2RejectedSS)
    expect(result.nextStepIndex).toBe(1)
  })

  // T858-S4: allDone cross-type consistency — R wf_done and non-R headStatus=done
  // both emit identical all-done stepStates; only mode differs (info vs sequence-complete).
  const allDone5 = steps5.map(code => ss(code, 'done'))

  it('T858-S4a: R wf_done → allDoneSS, mode=info, nextStepIndex=null', () => {
    const result = s({ tabTypeCode: 'R', tabReviewStatus: 'wf_done', workflowSteps: steps5 })
    expect(result.mode).toBe('info')
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual(allDone5)
  })

  it.each(['M', 'DS', 'D', 'T', 'TR'])(
    'T858-S4b: %s tab + headStatus=done → mode=sequence-complete, allDoneSS matches R wf_done stepStates',
    (tabTypeCode) => {
      const result = s({ tabTypeCode, workflowSteps: steps5, headType: null, headStatus: 'done', nextStepExists: false })
      expect(result.mode).toBe('sequence-complete')
      expect(result.nextStepIndex).toBeNull()
      expect(result.stepStates).toEqual(allDone5)
    },
  )

  // T858-S5: nextStepIndex positional invariance — same headType yields the same nextStepIndex
  // for both R tab and DS tab at every step position in the sequence.
  // Guards against any branch overriding or ignoring the normalSS nextStepIndex.
  it.each<[string, number]>([
    ['M',  0],
    ['DS', 1],
    ['D',  2],
    ['T',  3],
    ['TR', 4],
  ])(
    'T858-S5: headType=%s (position %i) → nextStepIndex=%i for both R tab and DS tab',
    (headType, expectedIndex) => {
      const rResult = s({
        tabTypeCode: 'R',
        tabReviewStatus: 'wf_in_progress',
        workflowSteps: steps5,
        headType,
        headStatus: 'pending',
      })
      expect(rResult.nextStepIndex).toBe(expectedIndex)

      const dsResult = s({
        tabTypeCode: 'DS',
        tabReviewStatus: 'pending_review',
        workflowSteps: steps5,
        headType,
        headStatus: 'pending',
      })
      expect(dsResult.nextStepIndex).toBe(expectedIndex)
    },
  )
})

// ── T869: verify current-step highlighting for an undecided M document ──
//
// Target: the M tab intentionally has no wfDecided branch.
// The R tab uses tabReviewStatus.startsWith('wf_') as its wfDecided gate and
// returns mode='workflow' while undecided.
// The M tab lacks this gate and branches on nextStepExists regardless of tabReviewStatus.
// Focus: verify that the M cell is 'highlight' when the M document itself is the head step.

describe('resolveWorkflowViewState — 미결정 M-doc 현재 단계 강조 (T869)', () => {
  const steps6m = ['R', 'M', 'DS', 'D', 'T', 'TR']

  // T869-S1: M tab with M as pending head -> M step highlighted, canNextAction=true.
  // This matches the R-tab result when headPending=true, but the M branch calls
  // nextAction() regardless of headStatus, so pending and in_progress both allow the action.
  // stepStates: R(index 0) < headIndex(1) → done; M(index 1) = headIndex → highlight.
  it('T869-S1: M 탭, M이 헤드(pending) → mode=next, canNextAction=true, M=highlight, R=done', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps6m,
      headType: 'M',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('M')
    expect(result.nextStepIndex).toBe(1)
    expect(result.stepStates).toEqual([
      ss('R',  'done'),
      ss('M',  'current'),
      ss('DS', 'future'),
      ss('D',  'future'),
      ss('T',  'future'),
      ss('TR', 'future'),
    ])
  })

  // T869-S2: M tab with M as in-progress head -> no headStatus gate, canNextAction=true.
  // The R branch sets canNextAction=false when headPending=false, but the M branch
  // never reads headStatus and always calls nextAction().
  // [feedback_memo_no_review_actions]: M tabs never reach review mode.
  it('T869-S2: M 탭, M이 헤드(in_progress) → mode=next, canNextAction=true (headStatus 게이트 없음, [feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps6m,
      headType: 'M',
      headStatus: 'in_progress',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('M')
    expect(result.nextStepIndex).toBe(1)
    expect(result.stepStates[1]).toEqual(ss('M', 'current'))
  })

  // T869-S3: M tab + tabReviewStatus=null (wfDecided=false for R) -> mode=next, not workflow.
  // The R tab returns mode='workflow' without a wf_ prefix.
  // The M branch runs first without reading tabReviewStatus, so the same input yields mode='next'.
  // DocWorkflow.vue renders from stepStates because M tabs have no wf-undecided placeholders.
  it('T869-S3: M 탭, tabReviewStatus=null → mode=next (wfDecided 게이트 없음, mode≠workflow)', () => {
    const result = s({
      tabTypeCode: 'M',
      tabReviewStatus: null,
      workflowSteps: steps6m,
      headType: 'M',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.mode).not.toBe('workflow')
    expect(result.canNextAction).toBe(true)
    expect(result.highlightStepCode).toBe('M')
    expect(result.nextStepIndex).toBe(1)
  })
})

// ── T871: verify highlight removal and info mode on entering wf_done ──
//
// Target: workflowViewState.ts wf_done branch (line 237) and allDoneSS(buildStepStates(..., allDone=true)).
//
// On entering wf_done, resolveWorkflowViewState must take the allDoneSS path and guarantee:
//   - mode = 'info'
//   - canNextAction = false
//   - highlightStepCode = null, nextStepCode = null, nextStepActive = false
//   - every stepState has visual='done' and className='done' without wf-next-action
//   - nextStepIndex = null
//
// T871-S2 additionally verifies that wf_done overrides headType/headStatus input with
// allDoneSS rather than normalSS.

describe('resolveWorkflowViewState — wf_done 강조 해제 (T871)', () => {
  // Seven-step sequence extends coverage beyond the earlier wf_done tests using steps5/steps6.
  const stepsLong = ['R', 'DS', 'D', 'P', 'L', 'T', 'TR']

  // T871-S1: R tab, wf_done, seven-step sequence.
  // wf_done -> allDoneSS -> mode=info, with every stepState done and using the check-circle icon.
  // nextStepIndex=null, highlightStepCode=null, canNextAction=false.
  it('T871-S1: R wf_done, 7-step 시퀀스 → mode=info, canNextAction=false, highlightStepCode=null, 모든 stepStates visual=done, nextStepIndex=null', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_done',
      workflowSteps: stepsLong,
    })
    expect(result.mode).toBe('info')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.nextStepCode).toBeNull()
    expect(result.nextStepActive).toBe(false)
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual(stepsLong.map(code => ss(code, 'done')))
  })

  // T871-S2: R tab, wf_done, with headType=D and headStatus=in_progress input.
  // The wf_done branch ignores headType/headStatus and uses allDoneSS, so the
  // all-done state must override normalSS highlighting.
  // No className may contain wf-next-action.
  it('T871-S2: R wf_done, headType=D / headStatus=in_progress 존재 → allDoneSS 덮어씀, 모든 className에 wf-next-action 미포함, nextStepIndex=null', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_done',
      workflowSteps: stepsLong,
      headType: 'D',
      headStatus: 'in_progress',
      headDocId: 'doc-d-1',
    })
    expect(result.mode).toBe('info')
    expect(result.nextStepIndex).toBeNull()
    result.stepStates.forEach((step) => {
      expect(step.visual).toBe('done')
      expect(step.className).toBe('done')
      expect(step.className).not.toContain('wf-next-action')
      expect(step.iconClass).toBe('check-circle')
    })
  })
})

// ── T867: verify that M-tab action bars exclude approve, reject, and review actions ──
//
// [feedback_memo_no_review_actions]: M tabs never return mode='review' or mode='rejected'
// from resolveWorkflowViewState.
// ReviewActionBar.vue renders its approve/reject/review v-else block only in review mode.
// Because the M branch (lines 287-291) runs before tabReviewStatus dispatch,
// no tabReviewStatus value can send an M tab into the review-action block.

describe('resolveWorkflowViewState — M-tab 액션 제외 (T867)', () => {
  const steps5m = ['R', 'M', 'DS', 'D', 'T']

  // T867-S1: M + nextStepExists=true, head=DS pending
  // M branch -> nextAction() -> mode='next'; approve/reject/review remain hidden.
  it('T867-S1: M + nextStepExists=true, head=DS pending → mode=next, mode ≠ review, mode ≠ rejected ([feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps5m,
      headType: 'DS',
      headStatus: 'pending',
      headDocId: null,
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.mode).not.toBe('review')
    expect(result.mode).not.toBe('rejected')
    expect(result.canNextAction).toBe(true)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R',  'done'),
      ss('M',  'done'),
      ss('DS', 'current'),
      ss('D',  'future'),
      ss('T',  'future'),
    ])
  })

  // T867-S2: M + nextStepExists=false (standalone completed state).
  // M branch -> noAction('info') -> mode='info'; approve/reject/review remain hidden.
  it('T867-S2: M + nextStepExists=false → mode=info, mode ≠ review ([feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: ['M'],
      headType: 'M',
      headStatus: 'in_progress',
      headDocId: 'doc-m-001',
      nextStepExists: false,
    })
    expect(result.mode).toBe('info')
    expect(result.mode).not.toBe('review')
    expect(result.mode).not.toBe('rejected')
    expect(result.canNextAction).toBe(false)
  })

  // T867-S3: M + tabReviewStatus='pending_review' + nextStepExists=true
  // Core [feedback_memo_no_review_actions] case.
  // A non-M type with tabReviewStatus='pending_review' would flow to noAction('review'),
  // but the earlier M branch preserves mode='next'.
  // Removing the M exclusion would regress this case to mode='review'.
  it('T867-S3: M + tabReviewStatus=pending_review + nextStepExists=true → mode=next (M 분기 우선, [feedback_memo_no_review_actions] 핵심)', () => {
    const result = s({
      tabTypeCode: 'M',
      tabReviewStatus: 'pending_review',
      workflowSteps: steps5m,
      headType: 'DS',
      headStatus: 'pending',
      nextStepExists: true,
    })
    expect(result.mode).toBe('next')
    expect(result.mode).not.toBe('review')
    expect(result.mode).not.toBe('rejected')
    expect(result.canNextAction).toBe(true)
  })

  // T867-S4: M + headStatus='done' -> sequence-complete guard returns before the M branch.
  // A fully completed sequence uses mode='sequence-complete', so approve/reject/review remain hidden.
  it('T867-S4: M + headStatus=done → mode=sequence-complete (가드 선처리), mode ≠ review ([feedback_memo_no_review_actions])', () => {
    const result = s({
      tabTypeCode: 'M',
      workflowSteps: steps5m,
      headType: null,
      headStatus: 'done',
      nextStepExists: false,
    })
    expect(result.mode).toBe('sequence-complete')
    expect(result.mode).not.toBe('review')
    expect(result.mode).not.toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepIndex).toBeNull()
    expect(result.stepStates).toEqual([
      ss('R',  'done'),
      ss('M',  'done'),
      ss('DS', 'done'),
      ss('D',  'done'),
      ss('T',  'done'),
    ])
  })
})

// ── T872: verify rejected DocWorkflow head-cell highlighting (visual=rejected) ──
//
// Target: workflowViewState.ts NR157 fix, ensuring the head cell becomes visual='rejected'.
// In DocWorkflow.vue stepStates rendering, the head cell receives
// s.className='wf-rejected dip-step-rejected' and s.iconClass='x-circle'.
//
// [feedback_head_viewed_nullcoalesce_trap]: headDocReviewStatus is normalized with ?? null,
// so undefined/null inputs are excluded from rejected detection. S3 verifies this boundary.

describe('resolveWorkflowViewState — rejected head cell 강조 (T872)', () => {
  const steps6r = ['R', 'M', 'DS', 'D', 'T', 'TR']

  // T872-S1: R tab + rejected T head in the middle of the sequence -> mode=rejected and rejected T cell.
  // className='wf-rejected dip-step-rejected', iconClass='x-circle'.
  // Also verify that earlier steps (R/M/DS/D) are done and the later TR step is future.
  it('T872-S1: R tab, head=T rejected (NR157) → mode=rejected, canNextAction=false, T cell visual=rejected + className + iconClass, 이전=done / 이후=future', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6r,
      headType: 'T',
      headStatus: 'in_progress',
      headDocId: 'doc-t-99',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.highlightStepCode).toBeNull()
    expect(result.headDocId).toBe('doc-t-99')
    expect(result.headDocLabel).toBe('T')
    const headCell = result.stepStates.find(st => st.code === 'T')!
    expect(headCell.visual).toBe('rejected')
    expect(headCell.className).toBe('wf-rejected dip-step-rejected')
    expect(headCell.iconClass).toBe('x-circle')
    expect(result.nextStepIndex).toBe(4)
    expect(result.stepStates).toEqual([
      ss('R',  'done'),
      ss('M',  'done'),
      ss('DS', 'done'),
      ss('D',  'done'),
      ss('T',  'rejected'),
      ss('TR', 'future'),
    ])
  })

  // T872-S2: steps before a rejected head are done and later steps are future, with DS at index 2.
  // headType='DS'(index 2) → R/M=done, DS=rejected, D/T/TR=future.
  // buildStepStates: idx < headIndex → done, idx === headIndex → headVisual('rejected'), idx > headIndex → future.
  it('T872-S2: R tab, head=DS rejected (sequence index 2) → done×2 / rejected(DS) / future×3, nextStepIndex=2', () => {
    const result = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6r,
      headType: 'DS',
      headStatus: 'in_progress',
      headDocReviewStatus: 'rejected',
    })
    expect(result.mode).toBe('rejected')
    expect(result.canNextAction).toBe(false)
    expect(result.nextStepIndex).toBe(2)
    expect(result.stepStates).toEqual([
      ss('R',  'done'),
      ss('M',  'done'),
      ss('DS', 'rejected'),
      ss('D',  'future'),
      ss('T',  'future'),
      ss('TR', 'future'),
    ])
  })

  // T872-S3: [feedback_head_viewed_nullcoalesce_trap] — headDocReviewStatus null / undefined →
  // Do not enter the rejected branch: after ?? null normalization, null === 'rejected' is false -> mode=next.
  // Verify that undefined is normalized identically at the null-coalescing boundary.
  it('T872-S3: [feedback_head_viewed_nullcoalesce_trap] headDocReviewStatus=null/undefined → rejected 분기 미진입, mode=next, head cell visual=current', () => {
    // null: explicit null does not trigger rejected detection.
    const resultNull = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6r,
      headType: 'DS',
      headStatus: 'pending',
      headDocReviewStatus: null,
    })
    expect(resultNull.mode).not.toBe('rejected')
    expect(resultNull.mode).toBe('next')
    const headCellNull = resultNull.stepStates.find(st => st.code === 'DS')!
    expect(headCellNull.visual).toBe('current')
    expect(headCellNull.className).toBe('current')

    // undefined: the ?? null boundary must treat undefined exactly like null.
    const resultUndef = s({
      tabTypeCode: 'R',
      tabReviewStatus: 'wf_in_progress',
      workflowSteps: steps6r,
      headType: 'DS',
      headStatus: 'pending',
      headDocReviewStatus: undefined,
    })
    expect(resultUndef.mode).not.toBe('rejected')
    expect(resultUndef.mode).toBe('next')
    const headCellUndef = resultUndef.stepStates.find(st => st.code === 'DS')!
    expect(headCellUndef.visual).toBe('current')
    expect(headCellUndef.className).toBe('current')
  })
})
