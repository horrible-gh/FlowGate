// 0071 D0008 §3.6 · §3.7 · §6.8 · §6.9 — the report and follow-up windows' rules. Pure functions over the card
// session's view, result rows and presentation; the windows draw them and call session commands only.
import type { TestSpecCaseDetail, TestSpecEvidence, TestSpecRecordRow, TestSpecView } from '@/features/document'
import type { TestCardPresentation } from '../testSpecModel'

export type ReportCause = 'impl' | 'scen' | 'env' | 'pass'
/** `manual` / `manualF` are the human check verdicts of the v8 mockup's decision record. */
export type ReportDecisionCause = ReportCause | 'manual' | 'manualF'
/** A case decision record. The server has no path for it yet (Q-8), so the session supplies none. */
export interface ReportDecision {
  readonly caseId: string
  readonly cause: ReportDecisionCause
  readonly reason: string | null
  readonly by: string | null
  readonly at: string | null
}
export const REPORT_CAUSES: readonly ReportCause[] = ['impl', 'scen', 'env', 'pass']
const DEFECT_CAUSES: ReadonlySet<ReportDecisionCause> = new Set(['impl', 'manualF', 'scen'])

export type ReportMode = 'running' | 'confirm' | 'complete' | 'previous'
/** ④ live full run, ⑤ / ⑥ on a valid result, otherwise the read-only previous result (§3.3). */
export function reportMode(presentation: Pick<TestCardPresentation, 'stage' | 'hasReport'>): ReportMode | null {
  if (presentation.stage === 4) return 'running'
  if (presentation.stage === 5) return 'confirm'
  if (presentation.stage === 6) return 'complete'
  return presentation.stage === 3 && presentation.hasReport ? 'previous' : null
}
const PREVIOUS_TAGS = new Set(['cancelled', 'prepare_failed', 'execution_failed', 'stale'])
/** Message key (under `window.report.`) of the title tag. */
export function reportTagKey(mode: ReportMode, resultKey: string): string {
  if (mode !== 'previous') return `tag_${mode}`
  return PREVIOUS_TAGS.has(resultKey) ? `tag_previous_${resultKey}` : 'tag_previous'
}

export type ReportCellState = 'pass' | 'fail' | 'blocked' | 'not_run' | 'running' | 'queued'
const CELL_BY_STATUS: Readonly<Record<string, ReportCellState>> = { PASS: 'pass', FAIL: 'fail', BLOCKED: 'blocked', NOT_RUN: 'not_run' }
const RESULTS = new Set(['PASS', 'FAIL', 'BLOCKED'])
const RUNNABLE = new Set(['case_selectable', 'suite_only'])

export interface ReportItem {
  readonly caseId: string
  readonly title: string
  readonly executionMode: string | null
  readonly required: boolean
  readonly requirement: string | null
  readonly expected: string | null
  readonly automationRef: string | null
  readonly runCommand: string | null
  /** The result row's verdict (PASS · FAIL · BLOCKED · NOT_RUN), or null. */
  readonly status: string | null
  readonly actual: string | null
  readonly evidence: readonly TestSpecEvidence[]
  readonly state: ReportCellState
  /** A confirmation target (§3.2): a failed / blocked result, or a human check without a result. */
  readonly flagged: boolean
  /** Flagged and not decided. */
  readonly pending: boolean
  /** A human check case without a result: its decision area is the human check one. */
  readonly manualCheck: boolean
  readonly retrying: boolean
  /** The single-case retry of this case ended without a result (§3.3, the retry row). */
  readonly retryOutcome: 'cancelled' | 'failed' | null
  readonly retryError: string | null
  readonly decision: ReportDecision | null
}

const stamp = (run: { readonly createdAt: string | null; readonly startedAt: string | null } | null) => run?.createdAt ?? run?.startedAt ?? ''

/**
 * The previous result (§3.3 · §3.4) as its record holds it: the stored rows with their own title, expected result,
 * mode and stop-on-failure, whatever the scenario says now. What the record does not hold (requirement, code) stays empty.
 * It is read only, so no case is a confirmation target.
 */
function previousItems(stored: readonly TestSpecRecordRow[]): ReportItem[] {
  return stored.map((row) => ({
    caseId: row.caseId, title: row.title ?? '', executionMode: row.executionMode, required: row.required, requirement: null,
    expected: row.expected, automationRef: null, runCommand: null, status: row.status, actual: row.actual, evidence: row.evidence,
    state: CELL_BY_STATUS[row.status ?? ''] ?? 'not_run', flagged: false, pending: false, manualCheck: false,
    retrying: false, retryOutcome: null, retryError: null, decision: null,
  }))
}

/**
 * The report items in case order. A previous result shows its stored record; during a full run no earlier result,
 * actual or evidence is shown as the run's own (the session carries no per-case result of the live run).
 */
export function reportItems(view: TestSpecView | null, cases: readonly TestSpecCaseDetail[], rows: readonly TestSpecRecordRow[],
  presentation: Pick<TestCardPresentation, 'stage' | 'hasReport' | 'runScope' | 'retryCaseId'>, decisions: readonly ReportDecision[] = []): ReportItem[] {
  if (reportMode(presentation) === 'previous') return previousItems(view?.details?.storedRows ?? [])
  const running = presentation.stage === 4
  const live = view?.activeRun ?? null
  const selected = new Set(live?.selectedCaseIds ?? [])
  const last = view?.lastExecution ?? null
  const retryEnded = !live && last && last.selectedCaseIds?.length === 1 && (last.status === 'cancelled' || last.status === 'failed')
    && stamp(last) >= stamp(view?.latest ?? null) ? last : null
  return cases.map((item) => {
    const row = running ? null : rows.find((r) => r.caseId === item.caseId) ?? null
    const status = row?.status ?? null
    const decision = decisions.find((d) => d.caseId === item.caseId) ?? null
    const manualCheck = item.executionMode === 'manual' && !RESULTS.has(status ?? '')
    const flagged = !running && (status === 'FAIL' || status === 'BLOCKED' || manualCheck)
    const runnable = item.executionMode === 'automated' && RUNNABLE.has(view?.caseCapabilities[item.caseId] ?? '')
    const state: ReportCellState = running
      ? (runnable && (selected.size === 0 || selected.has(item.caseId)) ? 'running' : 'queued')
      : CELL_BY_STATUS[status ?? ''] ?? 'not_run'
    const retryOutcome = retryEnded?.selectedCaseIds?.[0] === item.caseId ? (retryEnded.status === 'cancelled' ? 'cancelled' : 'failed') : null
    return {
      caseId: item.caseId, title: item.title ?? row?.title ?? '', executionMode: item.executionMode, required: item.required,
      requirement: item.fields.requirement, expected: row?.expected ?? item.fields.expected, automationRef: item.fields.automationRef ?? null, runCommand: item.fields.runCommand ?? null,
      status, actual: row?.actual ?? null, evidence: row?.evidence ?? [], state, flagged, pending: flagged && !decision, manualCheck,
      retrying: presentation.runScope === 'case' && presentation.retryCaseId === item.caseId,
      retryOutcome, retryError: retryOutcome ? retryEnded!.prepareRefused ?? retryEnded!.error : null, decision,
    }
  })
}

export type ReportLegend = Readonly<Record<ReportCellState, number>>
export function reportLegend(items: readonly ReportItem[]): ReportLegend {
  const out: Record<ReportCellState, number> = { pass: 0, fail: 0, blocked: 0, not_run: 0, running: 0, queued: 0 }
  for (const item of items) out[item.state]++
  return out
}

/** Cases whose failure stops the next step (실패 시 = 다음 단계 중단). */
export function reportGate(items: readonly ReportItem[]): ReportItem[] {
  return items.filter((item) => item.required && (item.state === 'fail' || item.state === 'blocked'))
}

/** Implementation defects and the decided cases that are not (the follow-up window's two lists, §6.9). */
export function reportDefects(items: readonly ReportItem[]): { defects: ReportItem[]; others: ReportItem[] } {
  const decided = items.filter((item) => item.decision)
  return { defects: decided.filter((item) => DEFECT_CAUSES.has(item.decision!.cause)), others: decided.filter((item) => !DEFECT_CAUSES.has(item.decision!.cause)) }
}

export type ReportFilter = 'flagged' | 'all'
/** ⑤ 확인 필요 n / 전체 N (default 확인 필요), ⑥ 확인 내역 n / 전체 N; the other modes list every case. */
export function reportDefaultFilter(mode: ReportMode, items: readonly ReportItem[]): ReportFilter {
  if (mode === 'confirm') return 'flagged'
  return mode === 'complete' && items.some((item) => item.flagged || item.decision) ? 'flagged' : 'all'
}
export function reportVisible(items: readonly ReportItem[], mode: ReportMode, filter: ReportFilter): ReportItem[] {
  if (filter === 'all' || (mode !== 'confirm' && mode !== 'complete')) return [...items]
  return items.filter((item) => item.flagged || item.decision)
}

/** The selection kept when it is listed, else the first pending, else the first listed case. */
export function reportSelection(visible: readonly ReportItem[], caseId: string | null): string | null {
  if (caseId && visible.some((item) => item.caseId === caseId)) return caseId
  return (visible.find((item) => item.pending) ?? visible[0])?.caseId ?? null
}
/** J / K: the neighbour in the current list, clamped at both ends (the mockup's keyboard). */
export function reportMove(visible: readonly ReportItem[], caseId: string | null, delta: 1 | -1): string | null {
  if (!visible.length) return null
  const index = visible.findIndex((item) => item.caseId === caseId)
  if (index < 0) return visible[0].caseId
  return visible[Math.max(0, Math.min(visible.length - 1, index + delta))].caseId
}
/** After a decision: the next pending case from the one after the current, once around (§3.7). */
export function reportNextPending(items: readonly ReportItem[], caseId: string | null): string | null {
  const start = items.findIndex((item) => item.caseId === caseId)
  for (let step = 1; step <= items.length; step++) {
    const item = items[(start + step + items.length) % items.length]
    if (item.pending && item.caseId !== caseId) return item.caseId
  }
  return null
}
/**
 * A rerun this window started, judged once it has ended (§3.7): a pass counts as decided (the next pending case is
 * selected), a failure or a cancelled / failed rerun keeps the case as a confirmation target.
 */
export function reportRerunOutcome(items: readonly ReportItem[], caseId: string, live: boolean): 'running' | 'passed' | 'kept' {
  const item = items.find((entry) => entry.caseId === caseId)
  if (live || item?.retrying) return 'running'
  return item?.status === 'PASS' && !item.pending ? 'passed' : 'kept'
}

/** The number the run list gives a run (#1 = the oldest kept run), or null when the list does not hold it (§6.8). */
export function reportRunNumber(history: readonly { readonly runId: string }[], runId: string | null | undefined): number | null {
  const index = runId ? history.findIndex((run) => run.runId === runId) : -1
  return index < 0 ? null : history.length - index
}

export type ReportCauseMode = 'ai' | 'human'
export type ReportPrimary = 'rerun' | 'confirm'
/** Enter's action: confirm the AI cause, or rerun when the AI says environment. None for human checks, the human-only mode and a retry. */
export function reportPrimary(item: ReportItem | null, causeMode: ReportCauseMode, aiCause: ReportCause | null): ReportPrimary | null {
  if (!item?.pending || item.retrying || item.manualCheck || causeMode === 'human') return null
  return aiCause === 'env' ? 'rerun' : 'confirm'
}

/** 통과 처리 needs its reason (§3.7): the record button stays disabled while it is blank. */
export const reportReasonReady = (reason: string) => reason.trim().length > 0

export interface ReportKeyFacts {
  readonly key: string
  readonly open: boolean
  readonly minimized: boolean
  readonly modifier: boolean
  /** The focus is in an input, a textarea, a select or an editable element. */
  readonly typing: boolean
  /** The focus is inside another modeless window. */
  readonly otherWindow: boolean
  /** The focus is on a button other than a list row. */
  readonly onButton: boolean
}
export type ReportKeyAction = 'next' | 'prev' | 'primary' | 'escape'
/** J/K (↓/↑) move, Enter runs the primary action, Esc closes a menu or the reason input (§3.7). */
export function reportKeyAction(facts: ReportKeyFacts): ReportKeyAction | null {
  if (!facts.open || facts.minimized || facts.modifier || facts.otherWindow) return null
  if (facts.key === 'Escape') return 'escape'
  if (facts.typing) return null
  if (facts.key === 'j' || facts.key === 'J' || facts.key === 'ArrowDown') return 'next'
  if (facts.key === 'k' || facts.key === 'K' || facts.key === 'ArrowUp') return 'prev'
  if (facts.key === 'Enter') return facts.onButton ? null : 'primary'
  return null
}

/** The window's per-card memory: selection, filter and the reason being typed. */
export interface ReportDraft {
  caseId: string | null
  /** The chosen filter belongs to the mode it was chosen in; a new mode starts from its default. */
  filter: ReportFilter | null; filterMode: ReportMode | null
  passFor: string | null; reason: string; cause: ReportCause | null
  /** The case whose rerun this window started and has not seen end yet. */
  rerunFor?: string | null
}
