// D031 — workflowViewState pure derive function
// Unifies DocWorkflow / DocInfoPanel / ReviewActionBar state from a single SSOT input.
// No side effects. No DOM. No store imports.

export type WorkflowViewMode =
  | 'workflow'
  | 'next'
  | 'review'
  | 'info'
  | 'q'
  | 'sequence-complete'
  | 'rejected'
  // 0119 B0001 (NR0009 §6.2/§6.3): decided workflow whose sequence row exists but has
  // ZERO items (every step deleted). The action bar offers no forward action and points
  // the user at the workflow strip's [시퀀스 수정] (Edit sequence) to re-add steps. Distinct from
  // 'sequence-complete' ([완료] [Complete]) — an empty sequence is a recovery state, not a finished one.
  | 'workflow-recover'

/** D031 §4.3 v2 — per-step visual. Priority: rejected > highlight > current > done > future. */
export type StepVisual = 'done' | 'highlight' | 'rejected' | 'current' | 'future'

/** D031 §4.3 v2 — single workflow band cell state. */
export interface StepState {
  code: string
  visual: StepVisual
  /** Canonical CSS modifier classes emitted by the SSOT — T843.
   * Contains wf-* classes for DocWorkflow strip and dip-* classes for DocInfoPanel.
   * visual → className mapping:
   *   done      → 'done'
   *   highlight → 'wf-next-action dip-step-clickable dip-step-active'
   *   rejected  → 'wf-rejected dip-step-rejected'
   *   current   → 'current'
   *   future    → 'future dip-step-disabled'
   */
  className: string
  /** Canonical AppIcon name emitted by the SSOT — T843.
   * visual → iconClass mapping:
   *   done      → 'check-circle'
   *   highlight → 'circle'
   *   rejected  → 'x-circle'
   *   current   → 'radio-button'
   *   future    → 'circle'
   */
  iconClass: string
}

/** D031 §3 — unified SSOT input. All fields are derived server-side; FE never re-derives raw columns. */
export interface WorkflowViewInput {
  /** Current tab's document type code (documents.type_code). */
  tabTypeCode: string | null | undefined
  /** Current tab's review status — the single SSOT for workflow state (documents.doc_review_status). */
  tabReviewStatus: string | null | undefined
  /** R document's workflow sequence definition (e.g. ['M','DS','D','T','TR']). */
  workflowSteps: string[]
  /** Group's current head step type (type of the in_progress doc or next unrealised step). */
  headType: string | null | undefined
  /** Head's index within `workflowSteps` (resolved by slot identity so repeated
   *  types colour the correct cell). When null, fall back to indexOf(headType). */
  headIndex?: number | null | undefined
  /** Group's current head state — 'pending' | 'in_progress' | 'done' | null. */
  headStatus: string | null | undefined
  /** Group's current head doc id (only when head is a real doc). */
  headDocId: string | null | undefined
  /** Head doc's own review status (only when head is a real doc). */
  headDocReviewStatus: string | null | undefined
  /** Whether a next step is defined in the R sequence. */
  nextStepExists: boolean
  /** Q document answer status. */
  qStatus?: string | null | undefined
}

/** D031 §4.3 — unified derived output consumed by DocWorkflow / DocInfoPanel / ReviewActionBar. */
export interface WorkflowViewState {
  /** ActionBar mode (D030 §4 matrix). */
  mode: WorkflowViewMode | null
  /** Whether the "proceed to next step" action is available. */
  canNextAction: boolean
  /**
   * @deprecated use stepStates. Kept for one migration cycle.
   * Previously: viewing-tab type code. Now: sequence-head step code.
   */
  currentStepCode: string | null
  /**
   * Workflow strip: step code to highlight as "act here" (warning 'wf-next-action' class).
   * null = no highlight.
   * @deprecated use stepStates.
   */
  highlightStepCode: string | null
  /**
   * DocInfoPanel "Next: xx" box step code.
   * @deprecated use stepStates + nextStepIndex.
   */
  nextStepCode: string | null
  /**
   * DocInfoPanel "Next" box active state (mirrors canNextAction, but also true for in-progress guard).
   * @deprecated use stepStates + nextStepIndex.
   */
  nextStepActive: boolean
  /** ActionBar head document label for display. */
  headDocLabel: string | null
  /** ActionBar head document id for navigation button. */
  headDocId: string | null
  /**
   * D030 §4 #7 multi-activation flag: true when the highlighted next step is a design-series step
   * (D / P / L / DB), signalling that D+P+L+DB+V must all be activated together.
   * Consuming code handles the actual multi-activation; this function only flags the condition.
   * @deprecated use stepStates.
   */
  highlightDesignSeries: boolean
  /**
   * D031 §4.3 v2 — per-step visual state. Same order and length as workflowSteps input.
   * DocWorkflow renders via v-for; DocInfoPanel indexes via nextStepIndex.
   */
  stepStates: StepState[]
  /** D031 §4.3 v2 — index into stepStates for the "Next" box. null when no next step. */
  nextStepIndex: number | null
}

// Design-series step types that trigger multi-activation (D030 §4 #7).
const DESIGN_SERIES = new Set(['D', 'P', 'L', 'DB'])

type StepStateSlice = Pick<WorkflowViewState, 'stepStates' | 'nextStepIndex'>

// visual → (className, iconClass) canonical mapping (T843 SSOT emit)
// done      → 'done'                                               | 'check-circle'
// highlight → 'wf-next-action dip-step-clickable dip-step-active'  | 'circle'
// rejected  → 'wf-rejected dip-step-rejected'                     | 'x-circle'
// current   → 'current'                                            | 'radio-button'
// future    → 'future dip-step-disabled'                           | 'circle'
const STEP_CLASS: Record<StepVisual, string> = {
  done:      'done',
  highlight: 'wf-next-action dip-step-clickable dip-step-active',
  rejected:  'wf-rejected dip-step-rejected',
  current:   'current',
  future:    'future dip-step-disabled',
}
const STEP_ICON: Record<StepVisual, string> = {
  done:      'check-circle',
  highlight: 'circle',
  rejected:  'x-circle',
  current:   'radio-button',
  future:    'circle',
}

/**
 * D031 §4.3 v2 — Derive per-step visual array from head position.
 * Steps before head → done. Head → visual per priority. Steps after head → future.
 * allDone=true overrides all steps to done (wf_done / sequence-complete cases).
 * headType null or absent from workflowSteps → all steps future.
 */
function buildStepStates(
  workflowSteps: string[],
  headType: string | null,
  headDocReviewStatus: string | null,
  allDone = false,
  providedHeadIndex: number | null = null,
): StepStateSlice {
  if (allDone) {
    return {
      stepStates: workflowSteps.map(code => ({ code, visual: 'done' as const, className: STEP_CLASS.done, iconClass: STEP_ICON.done })),
      nextStepIndex: null,
    }
  }
  // Prefer the identity-resolved index (handles repeated types); fall back to
  // type lookup when not supplied.
  const headIndex = (providedHeadIndex != null && providedHeadIndex >= 0 && providedHeadIndex < workflowSteps.length)
    ? providedHeadIndex
    : (headType != null ? workflowSteps.indexOf(headType) : -1)
  if (headIndex === -1) {
    return {
      stepStates: workflowSteps.map(code => ({ code, visual: 'future' as const, className: STEP_CLASS.future, iconClass: STEP_ICON.future })),
      nextStepIndex: null,
    }
  }
  // Non-rejected head paints 'current' (blue) — the head is the doc being reported/
  // acted on, not a far-off "go here" target. 5be8bee had switched this to 'highlight'
  // (warning yellow, wf-next-action), turning every workflow head yellow; the product
  // owner flagged the yellow next-action as a regression (group 0104). Restore the
  // pre-5be8bee blue. (DocWorkflow/DocInfoPanel click handlers accept both visuals.)
  const headVisual: StepVisual = headDocReviewStatus === 'rejected'
    ? 'rejected'
    : 'current'
  const stepStates: StepState[] = workflowSteps.map((code, idx) => {
    const visual = (idx < headIndex ? 'done' : idx === headIndex ? headVisual : 'future') as StepVisual
    return { code, visual, className: STEP_CLASS[visual], iconClass: STEP_ICON[visual] }
  })
  return { stepStates, nextStepIndex: headIndex }
}

function noAction(
  mode: WorkflowViewMode,
  tabTypeCode: string | null,
  ss: StepStateSlice,
): WorkflowViewState {
  return {
    mode,
    canNextAction: false,
    currentStepCode: tabTypeCode,
    highlightStepCode: null,
    nextStepCode: null,
    nextStepActive: false,
    headDocLabel: null,
    headDocId: null,
    highlightDesignSeries: false,
    ...ss,
  }
}

function nextAction(
  tabTypeCode: string | null,
  headType: string | null,
  headDocId: string | null,
  ss: StepStateSlice,
): WorkflowViewState {
  return {
    mode: 'next',
    canNextAction: true,
    currentStepCode: tabTypeCode,
    highlightStepCode: headType,
    nextStepCode: headType,
    nextStepActive: true,
    headDocLabel: headType,
    headDocId,
    highlightDesignSeries: DESIGN_SERIES.has(headType ?? ''),
    ...ss,
  }
}

export function resolveWorkflowViewState(input: WorkflowViewInput): WorkflowViewState {
  const tabTypeCode = input.tabTypeCode ?? null
  const headType = input.headType ?? null
  const headDocId = input.headDocId ?? null
  const headStatus = input.headStatus ?? null
  const headDocReviewStatus = input.headDocReviewStatus ?? null

  const normalSS = buildStepStates(input.workflowSteps, headType, headDocReviewStatus, false, input.headIndex ?? null)
  const allFutureSS = buildStepStates(input.workflowSteps, null, null)
  const allDoneSS = buildStepStates(input.workflowSteps, null, null, true)

  if (!tabTypeCode) return noAction('review', null, { stepStates: [], nextStepIndex: null })

  // ── Decided-but-empty workflow recovery (0119 B0001 / NR0009 §6.2/§6.3) ───
  // The server reports headStatus='empty' when a decided workflow's sequence row still
  // exists but every step item was deleted. This is the "결정됨+빈" (decided+empty) zombie — NOT
  // a finished workflow. Intercept BEFORE the R/B and non-R branches so it cannot fall through
  // to the phantom mode='next' (an R/B head=null "다음 단계" [next step] affordance) or to
  // mode='sequence-complete' ([완료] [Complete] + group-chain advance) on a sibling tab. The
  // only legitimate workflow terminus is the AC final-approval gate; an empty sequence routes
  // to recovery instead. The matching DocWorkflow strip keeps its decidedEmpty hint +
  // [시퀀스 수정] (Edit sequence) button to re-add steps.
  if (headStatus === 'empty') {
    return noAction('workflow-recover', tabTypeCode, allFutureSS)
  }

  // ── Workflow-root tab (R/B) ───────────────────────────────────────────────
  if (tabTypeCode === 'R' || tabTypeCode === 'B') {
    // D030 §4 #1: workflow undecided → only action is [Decide workflow].
    // "Decided" is judged by TWO signals, not the doc_review_status prefix alone.
    // The prefix is a single derived string that goes stale on a tab that missed the
    // live SSE while idle (the R0001 / NR0003 regression: an out-of-band decision left
    // the cached status pre-decision, so the [Workflow Decision] button reappeared and every
    // re-click hit 409 already_decided). The authority signal is the materialized
    // head — server-side, workflow_head_type is only ever populated once a decision
    // exists — so a present headType means decided even when the cached prefix lies.
    // (We use headType, not workflowSteps.length: the strip can be fed sequence cells
    //  for an undecided R to render all-future, so length is not a decided signal.)
    const wfDecided = (input.tabReviewStatus ?? '').startsWith('wf_') || headType != null
    if (!wfDecided) return noAction('workflow', tabTypeCode, allFutureSS)

    // D030 §4 wf_done: sequence complete — no further action
    if (input.tabReviewStatus === 'wf_done') return noAction('info', tabTypeCode, allDoneSS)

    // NR157 fix: rejected head — mode='rejected', head cell visual='rejected'
    if (headDocReviewStatus === 'rejected') {
      return {
        mode: 'rejected',
        canNextAction: false,
        currentStepCode: tabTypeCode,
        highlightStepCode: null,
        nextStepCode: null,
        nextStepActive: false,
        headDocLabel: headType,
        headDocId,
        highlightDesignSeries: false,
        ...normalSS,
      }
    }

    // D030 §4 #2/#3: workflow decided, sequence in progress.
    // PM decision (D031 §9): R tab stays in 'next' branch regardless of headStatus.
    // When head=in_progress: highlight the head step (user must go to that tab to approve),
    // but canNextAction=false (guard: cannot advance while a step is in progress).
    const headPending = !headStatus || headStatus === 'pending'
    return {
      mode: 'next',
      canNextAction: headPending,
      currentStepCode: tabTypeCode,
      highlightStepCode: headType,
      nextStepCode: headType,
      nextStepActive: true,
      headDocLabel: headType,
      headDocId,
      highlightDesignSeries: false,
      ...normalSS,
    }
  }

  // ── Conversation (CH) tab ─────────────────────────────────────────────────
  // TR0044.0010 rev3: a CH document is a normal workflow node, NOT a special mode.
  // The reviewer wants its action bar (next-action button) kept so the workflow can
  // advance from a conversation like any other doc — so CH falls through to the
  // shared non-R logic below. The chat surface (bubbles + composer + mention-copy) lives
  // in the content-area ConversationView, independent of the action bar. The "don't
  // show [Proceed to next step]/[Create empty doc] for a conversation" reject is handled where it
  // belongs: at the *creation* edge — when the NEXT step is CH, ReviewActionBar shows
  // a single [Create conversation doc] auto-create button instead of the split control.

  // ── Non-R: sequence-complete guard (head done = entire workflow finished) ──
  if (headStatus === 'done') return noAction('sequence-complete', tabTypeCode, allDoneSS)

  // ── Q tab ─────────────────────────────────────────────────────────────────
  if (tabTypeCode === 'Q') {
    // D030 §4 #5: answered + next exists → proceed
    const canNextAction = input.qStatus === 'done' && input.nextStepExists
    if (canNextAction) return nextAction(tabTypeCode, headType, headDocId, normalSS)
    // D030 §4 #6: unanswered or no next → Q guidance
    return noAction('q', tabTypeCode, normalSS)
  }

  // ── M tab ─────────────────────────────────────────────────────────────────
  if (tabTypeCode === 'M') {
    // D030 §4 #4: next exists → [Next step]; else → [Complete]
    if (input.nextStepExists) return nextAction(tabTypeCode, headType, headDocId, normalSS)
    return noAction('info', tabTypeCode, normalSS)
  }

  // ── Non-R doc types: DS / D / T / TS / N / NR / TR / TSR / P / DB / L / V / C / AC / RJ ──
  const reviewStatus = input.tabReviewStatus ?? null

  // D030 §4 #7: approved
  if (reviewStatus === 'approved') {
    if (input.nextStepExists) return nextAction(tabTypeCode, headType, headDocId, normalSS)
    return noAction('info', tabTypeCode, normalSS)
  }

  // D030 §4 #8: rejected → rework toolbar
  if (reviewStatus === 'rejected') return noAction('rejected', tabTypeCode, normalSS)

  // D030 §4 #9: pending_review / revised (and any other status) → review panel
  return noAction('review', tabTypeCode, normalSS)
}
