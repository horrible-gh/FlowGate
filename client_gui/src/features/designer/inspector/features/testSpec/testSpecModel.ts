// flowgate.v02.0048 T#11 — the test section's rules (D#1 §8, §10.1 테스트 column, §10.2 T#11 row). Pure
// functions over the target and the test view of its document (`GET /documents/{doc_id}/test-document`,
// normalized by `@/features/document`): which form the TS has, the Case tally, the run state, the run
// history rows and whether starting and cancelling a run are possible now.
//
// Form: a specification TS (contract 2) — or a TSR assembled from one — runs here: [▶ 실행] starts a server
// run of every automated Case, [■ 취소] cancels the live run. A legacy executable TS (contract 1) and any
// other answer keep their run in the v0.1 screen (↗ 열기); this section then shows no run operation.
//
// Operations (D#1 §8.3 rule 0): reading and ↗ 열기 are queries, always possible; starting and cancelling a
// run are changes. Starting is disabled with one reason, in the order of rule 6-1: ownership conflict,
// revision view, disposed group, AI running, then this feature's own — the server refused the user
// (403: admin or perm_test_run, D#1 §8.2 — there is no permission list on the client, so the operation is
// enabled until the server says no), the report is already approved, the TS is not approved (a TS that
// already ran may run again while its result is reviewed or revised), no automated Case the server can
// run (read on a TS only: a TSR's view carries no Cases), a run in flight, a call in flight. Cancelling is offered while a run is live and is disabled by the
// read-only views, the permission refusal, a cancel already accepted and a call in flight; the server does
// not refuse a cancel for a disposed group or an AI run, so neither blocks reclaiming a runaway run.
// Not offered (D#1 §8.4, §10.2): a single Case's run, result entry, the Case editor (all ↗ 열기), the
// Graph TS Node run (Graph 실행 연동) and the separate Case document kind.
import type { InspectorCardKind, InspectorReadOnly } from '../../inspectorTarget'

/** The parts of the target the section reads. */
export interface TestSpecTargetFacts {
  readonly docId: string | null
  readonly docType: string | null
  readonly cardKind: InspectorCardKind
}

/** Document types the section is registered for (D#1 §5.3: V is left out by `docTypes`, not hidden here). */
export const TEST_SPEC_DOC_TYPES = ['TS', 'TSR'] as const

/** The document whose test view the section reads: the selected Node's own TS or TSR. */
export function testSpecTargetDocId(target: TestSpecTargetFacts | null): string | null {
  return target?.docId ?? null
}

/** The parts of a run record the rules read (the `@/features/document` TestSpecRun). */
export interface TestSpecRunFacts {
  readonly runId: string
  readonly status: string | null
  readonly phase: string | null
  readonly overall: string | null
  readonly runKind: string | null
  readonly caseTotal: number | null
  readonly selectedCases: number
  readonly reportedCases: number
  readonly counts: TestSpecCountsFacts | null
  readonly error: string | null
  readonly prepareRefused: string | null
  readonly startedAt: string | null
  readonly finishedAt: string | null
  readonly createdAt: string | null
}

export interface TestSpecCountsFacts {
  readonly total: number
  readonly pass: number
  readonly fail: number
  readonly blocked: number
  readonly notRun: number
}

/** The parts of the test view the rules read (the `@/features/document` TestSpecView). */
export interface TestSpecViewFacts {
  readonly docId: string
  readonly kind: string
  readonly contractVersion: number | null
  readonly tsDocId: string | null
  readonly tsReviewStatus: string | null
  readonly tsrDocId: string | null
  readonly tsrReviewStatus: string | null
  readonly cases: readonly { readonly caseId: string; readonly executionMode: string | null; readonly required: boolean }[]
  readonly caseCapabilities: Readonly<Record<string, string>>
  readonly specErrors: number
  readonly latest: TestSpecRunFacts | null
  readonly activeRun: TestSpecRunFacts | null
  readonly lastExecution: TestSpecRunFacts | null
  readonly history: readonly TestSpecRunFacts[]
  readonly effective: { readonly overall: string | null; readonly counts: TestSpecCountsFacts | null } | null
  readonly basisValid: boolean | null
  readonly basisState: string | null
  readonly gate: { readonly applies: boolean; readonly passed: boolean; readonly overall: string | null } | null
}

/** spec = a specification TS (or its TSR) with a TS to run; legacy = an executable TS of the v0.1 runner. */
export type TestSpecForm = 'spec' | 'legacy' | 'none'

export function testSpecForm(view: TestSpecViewFacts | null): TestSpecForm {
  if (view === null) return 'none'
  if (view.contractVersion === 2 && view.tsDocId !== null && (view.kind === 'TS' || view.kind === 'TSR')) return 'spec'
  if (view.contractVersion === 1 && (view.kind === 'TS' || view.kind === 'TSR')) return 'legacy'
  return 'none'
}

/** Capabilities of a Case the server runner executes (`spec_execution_service.automated_selection`). */
const RUNNABLE_CAPABILITIES = new Set(['case_selectable', 'suite_only'])

export interface TestSpecTally {
  readonly total: number
  readonly required: number
  readonly automated: number
  readonly manual: number
  readonly external: number
  /** Automated Cases the server runner can execute (what [▶ 실행] runs). */
  readonly runnable: number
}

/** Case tally of a specification TS by execution mode (other modes count in `total` only). */
export function testSpecTally(view: TestSpecViewFacts | null): TestSpecTally {
  const cases = view?.cases ?? []
  const mode = (name: string) => cases.filter((item) => item.executionMode === name)
  return {
    total: cases.length,
    required: cases.filter((item) => item.required).length,
    automated: mode('automated').length,
    manual: mode('manual').length,
    external: mode('external').length,
    runnable: mode('automated').filter((item) => RUNNABLE_CAPABILITIES.has(view?.caseCapabilities[item.caseId] ?? '')).length,
  }
}

export type TestSpecRunStateKey =
  | 'queued'
  | 'preparing'
  | 'executing'
  | 'finalizing'
  | 'cancelling'
  | 'prepare_failed'
  | 'execution_failed'
  | 'cancelled'
  | 'stale'
  | 'unchecked'
  | 'done'
  | 'none'

export type TestSpecTone = 'info' | 'success' | 'danger' | 'warning' | 'neutral'

export interface TestSpecRunState {
  readonly key: TestSpecRunStateKey
  /** A run is admitted or in flight. */
  readonly active: boolean
  readonly runId: string | null
  /** Cases the live run reported / selected. */
  readonly done: number
  readonly total: number
  /** Verdict of the valid result (done / stale / unchecked). */
  readonly overall: string | null
  /** Prepare refusal or execution error code of the newest run, when it produced no result. */
  readonly error: string | null
  readonly tone: TestSpecTone
}

const ACTIVE_PHASES = new Set(['queued', 'preparing', 'executing', 'finalizing'])

const stamp = (run: TestSpecRunFacts | null): string => run?.createdAt ?? run?.startedAt ?? ''

const LIVE_STATUSES = new Set(['running', 'cancelling'])

/**
 * The run in flight: the view's live run, or else its newest server run while that is still running or
 * being cancelled and newer than the last result. The server reads the run list and the live run
 * separately, so one answer can carry a run that is just being cancelled in its history but not as the
 * live run; that run stays in flight here until the server's next event reads it again as finished.
 */
export function testSpecLiveRun(view: TestSpecViewFacts | null): TestSpecRunFacts | null {
  if (view === null) return null
  if (view.activeRun) return view.activeRun
  const last = view.lastExecution
  return last && LIVE_STATUSES.has(last.status ?? '') && stamp(last) >= stamp(view.latest) ? last : null
}

/**
 * The run state of a specification view — the same reading as the v0.1 screen's run pill
 * (`testSpecRun.ts specRunState`): a run in flight (`testSpecLiveRun`) by its phase; otherwise the newest server run when it
 * failed or was cancelled after the last result; otherwise the valid result (stale / unchecked / done).
 */
export function testSpecRunState(view: TestSpecViewFacts | null): TestSpecRunState {
  const base = { runId: null, done: 0, total: 0, overall: null, error: null }
  if (view === null || testSpecForm(view) !== 'spec') return { ...base, key: 'none', active: false, tone: 'neutral' }
  const live = testSpecLiveRun(view)
  if (live) {
    const phase = live.phase ?? 'queued'
    const key: TestSpecRunStateKey = live.status === 'cancelling' ? 'cancelling' : ACTIVE_PHASES.has(phase) ? (phase as TestSpecRunStateKey) : 'executing'
    return { ...base, key, active: true, runId: live.runId, done: live.reportedCases, total: live.selectedCases || live.caseTotal || 0, tone: 'info' }
  }
  const latest = view.latest
  const last = view.lastExecution
  if (last && (last.status === 'failed' || last.status === 'cancelled') && stamp(last) >= stamp(latest)) {
    if (last.status === 'cancelled') return { ...base, key: 'cancelled', active: false, runId: last.runId, tone: 'warning' }
    return {
      ...base,
      key: last.prepareRefused ? 'prepare_failed' : 'execution_failed',
      active: false,
      runId: last.runId,
      error: last.prepareRefused ?? last.error,
      tone: 'danger',
    }
  }
  const overall = view.effective?.overall ?? latest?.overall ?? null
  if (!latest && !view.effective) return { ...base, key: 'none', active: false, tone: 'neutral' }
  if (view.basisValid === false) return { ...base, key: 'stale', active: false, overall, tone: 'warning' }
  if (view.basisState === 'unchecked') return { ...base, key: 'unchecked', active: false, overall, tone: 'neutral' }
  if (!latest) return { ...base, key: 'none', active: false, tone: 'neutral' }
  return { ...base, key: 'done', active: false, overall, tone: overall === 'PASS' ? 'success' : overall === 'FAIL' ? 'danger' : 'warning' }
}

/** Result counts shown: the valid result's, else the newest record's. */
export function testSpecCounts(view: TestSpecViewFacts | null): TestSpecCountsFacts | null {
  return view?.effective?.counts ?? view?.latest?.counts ?? null
}

/** Verdicts the server computes; anything else is shown as the raw value's own key `other`. */
const VERDICTS = new Set(['PASS', 'FAIL', 'BLOCKED', 'NOT_RUN'])

/** Message key (under `gui.inspector.testSpec.`) of a verdict. */
export function testSpecVerdictKey(overall: string | null): string {
  return overall && VERDICTS.has(overall) ? `verdict.${overall.toLowerCase()}` : 'verdict.other'
}

const RUN_STATUSES = new Set(['running', 'cancelling', 'passed', 'failed', 'cancelled'])

/** Message key of a run record's status. */
export function testSpecStatusKey(status: string | null): string {
  return status && RUN_STATUSES.has(status) ? `status.${status}` : 'status.other'
}

export interface TestSpecHistoryRow {
  readonly runId: string
  readonly statusKey: string
  readonly verdictKey: string | null
  readonly counts: TestSpecCountsFacts | null
  readonly at: string | null
}

/** The newest runs first, at most `limit` (the server already keeps the newest 30). */
export function testSpecHistory(view: TestSpecViewFacts | null, limit = 5): TestSpecHistoryRow[] {
  if (view === null || testSpecForm(view) !== 'spec') return []
  return view.history.slice(0, limit).map((run) => ({
    runId: run.runId,
    statusKey: testSpecStatusKey(run.status),
    verdictKey: run.overall ? testSpecVerdictKey(run.overall) : null,
    counts: run.counts,
    at: run.finishedAt ?? run.startedAt ?? run.createdAt,
  }))
}

export type TestSpecOperation = 'read' | 'open' | 'run' | 'cancel'

/** Reading and opening are queries; starting and cancelling a run change the server state (D#1 §8.3 rule 0). */
export const TEST_SPEC_OPERATION_CLASS: Readonly<Record<TestSpecOperation, 'change' | 'query'>> = {
  read: 'query',
  open: 'query',
  run: 'change',
  cancel: 'change',
}

export interface TestSpecInput {
  readonly readOnly: InspectorReadOnly
  readonly groupDisposed: boolean
  readonly aiRunning: boolean
  /** The test view of the target document, null before it is read. */
  readonly view: TestSpecViewFacts | null
  /** The server refused this user a run call on this document (403). */
  readonly denied: boolean
  /** A start or cancel is waiting for the server or for the read after it. */
  readonly busy: boolean
}

export interface TestSpecAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.testSpec.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type TestSpecAvailability = Readonly<Record<TestSpecOperation, TestSpecAvailabilityItem>>

const RERUN_STATUSES = new Set(['pending_review', 'revised'])

/** Why starting a run is disabled: one reason, in the order of D#1 §8.3 rule 6-1. */
export function testSpecRunDisabledReason(input: TestSpecInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed) return 'reason.disposed'
  if (input.aiRunning) return 'reason.ai_running'
  if (input.denied) return 'reason.forbidden'
  const view = input.view
  if (view === null) return 'reason.busy'
  if (view.tsrReviewStatus === 'approved') return 'reason.report_approved'
  const approved = view.tsReviewStatus === 'approved' || (RERUN_STATUSES.has(view.tsReviewStatus ?? '') && view.history.length > 0)
  if (!approved) return 'reason.not_approved'
  // Only a TS view carries its Cases; a TSR's run is judged by the server (422 no_automated_bindings).
  if (view.kind === 'TS' && (view.specErrors > 0 || testSpecTally(view).runnable === 0)) return 'reason.no_automated'
  if (testSpecLiveRun(view)) return 'reason.run_active'
  if (input.busy) return 'reason.busy'
  return null
}

/** Why cancelling the live run is disabled (read-only views, the permission refusal, an accepted cancel, a call in flight). */
export function testSpecCancelDisabledReason(input: TestSpecInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.denied) return 'reason.forbidden'
  if (testSpecLiveRun(input.view)?.status === 'cancelling') return 'reason.cancelling'
  if (input.busy) return 'reason.busy'
  return null
}

/**
 * Reading and ↗ 열기 are always possible. On a specification view, [▶ 실행] shows while no run is live and
 * [■ 취소] while one is; each is disabled with the first reason that holds. A legacy or unknown view shows
 * neither (its run is in the v0.1 screen). Every ownership (a document feature, D#1 §8.3 rules 2-4).
 */
export function testSpecAvailability(input: TestSpecInput): TestSpecAvailability {
  const spec = testSpecForm(input.view) === 'spec'
  const live = spec && testSpecLiveRun(input.view) !== null
  const hidden: TestSpecAvailabilityItem = { visible: false, enabled: false, reason: null }
  const item = (visible: boolean, reason: string | null): TestSpecAvailabilityItem =>
    visible ? { visible: true, enabled: reason === null, reason } : hidden
  return {
    read: { visible: true, enabled: true, reason: null },
    open: { visible: true, enabled: true, reason: null },
    run: item(spec && !live, testSpecRunDisabledReason(input)),
    cancel: item(live, testSpecCancelDisabledReason(input)),
  }
}

/** Kinds of a refused call or a failed read (the `@/features/document` testSpecErrorOf kinds). */
export type TestSpecFailureKind =
  | 'forbidden'
  | 'running'
  | 'disposed'
  | 'not_approved'
  | 'report_approved'
  | 'origin_pending'
  | 'no_automated'
  | 'conflict'
  | 'invalid'
  | 'not_found'
  | 'network'
  | 'other'

/** Message key of a refused start / cancel; the view is read again after every call. */
export function testSpecFailureKey(operation: 'run' | 'cancel', kind: TestSpecFailureKind): string {
  if (kind === 'other' || kind === 'invalid' || kind === 'conflict') return `failure.${operation}_${kind}`
  return `failure.${kind}`
}

/** A refusal that keeps the run operations disabled for this document: the server's permission answer. */
export function testSpecDeniedBy(kind: TestSpecFailureKind): boolean {
  return kind === 'forbidden'
}

/** Message key of a failed read of the test view. */
export function testSpecErrorKey(kind: TestSpecFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' ? `error.${kind}` : 'error.other'
}

// --- flowgate.v02.0051 stage 2 — the v7 테스트 card lines (0035 test.*: 지시 (TS) · 케이스 (TSC) · 결과 (TSR)) ---

export type TestSpecSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface TestSpecSlotState {
  /** `review_status.*` / `slot_state.*` are the Frame's wordings; `state.*` this feature's run states. */
  readonly key: string
  readonly raw: string
  readonly tone: TestSpecSlotTone
}

export interface TestSpecSlot {
  readonly key: 'instruction' | 'cases' | 'result'
  readonly kind: 'document' | 'result'
  readonly form: boolean
  readonly id: string | null
  readonly empty: boolean
  readonly emptyKey: string | null
  readonly state: TestSpecSlotState | null
  /** Which summary the line shows: the Case tally, the result counts, or the run progress. */
  readonly summary: 'tally' | 'counts' | 'progress' | null
  readonly progress: number | null
  readonly focus: boolean
  readonly open: 'enabled' | 'none'
  readonly write: 'disabled' | 'none'
}

const REVIEW_TONES: Readonly<Record<string, TestSpecSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

function reviewState(status: string | null): TestSpecSlotState | null {
  const review = status?.trim() ?? ''
  return review ? { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' } : null
}

const RUN_TONES: Readonly<Record<TestSpecTone, TestSpecSlotTone>> = { info: 'running', success: 'success', danger: 'danger', warning: 'warning', neutral: 'neutral' }

/**
 * The three v7 lines from the test view and the target. Without a view yet, the selected Node's own
 * document fills its line and the others wait; the Case line stands for the Cases inside the TS
 * (contract 2 — a separate Case document does not exist in 0048) and carries the tally only.
 */
export function testSpecSlots(view: TestSpecViewFacts | null, target: TestSpecTargetFacts): readonly TestSpecSlot[] {
  const type = target.docType?.trim().toUpperCase() ?? ''
  const tsId = view ? (view.kind === 'TS' ? view.docId : view.tsDocId) : type === 'TS' ? target.docId : null
  const tsrId = view ? (view.kind === 'TSR' ? view.docId : view.tsrDocId) : type === 'TSR' ? target.docId : null
  const tsState = view ? reviewState(view.tsReviewStatus) : null
  const form = testSpecForm(view)
  const run = testSpecRunState(view)
  const live = run.active && !!run.total

  const instruction: TestSpecSlot = tsId
    ? { key: 'instruction', kind: 'document', form: false, id: tsId, empty: false, emptyKey: null, state: tsState, summary: null, progress: null, focus: tsState?.raw === 'pending_review' || tsState?.raw === 'rejected', open: 'enabled', write: 'none' }
    : { key: 'instruction', kind: 'document', form: false, id: null, empty: true, emptyKey: 'empty.write', state: null, summary: null, progress: null, focus: false, open: 'none', write: 'disabled' }

  const cases: TestSpecSlot =
    form === 'spec'
      ? { key: 'cases', kind: 'document', form: true, id: null, empty: false, emptyKey: null, state: null, summary: 'tally', progress: null, focus: false, open: 'none', write: 'none' }
      : { key: 'cases', kind: 'document', form: true, id: null, empty: true, emptyKey: form === 'legacy' ? 'empty.legacy' : 'empty.after_run', state: null, summary: null, progress: null, focus: false, open: 'none', write: 'none' }

  const resultState: TestSpecSlotState | null =
    view && run.key !== 'none' ? { key: `state.${run.key}`, raw: run.key, tone: RUN_TONES[run.tone] } : view ? reviewState(view.tsrReviewStatus) : null
  const hasResult = tsrId !== null || (view !== null && run.key !== 'none')
  const result: TestSpecSlot = hasResult
    ? {
        key: 'result',
        kind: 'result',
        form: false,
        id: tsrId,
        empty: false,
        emptyKey: null,
        state: resultState,
        summary: live ? 'progress' : testSpecCounts(view) ? 'counts' : null,
        progress: live ? Math.round((run.done / run.total) * 100) : null,
        focus: run.overall === 'fail' || view?.tsrReviewStatus === 'pending_review' || view?.tsrReviewStatus === 'rejected',
        open: tsrId ? 'enabled' : 'none',
        write: 'none',
      }
    : { key: 'result', kind: 'result', form: false, id: null, empty: true, emptyKey: 'empty.after_run', state: null, summary: null, progress: null, focus: false, open: 'none', write: 'none' }

  return [instruction, cases, result]
}
