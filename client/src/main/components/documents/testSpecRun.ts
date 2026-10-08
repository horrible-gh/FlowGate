/**
 * flowgate.default.0684 T#3 (D#1 §3-8, §6): the contract-2 run state shared by the TS/TSR
 * body and the action bar.
 *
 * TestDocumentBody owns the `/test-document` read (and its polling while a run is live);
 * it publishes the view here so the action bar's run-state pill and [run again] read the same
 * answer instead of a second request. Every contract-2 run goes through one entry point,
 * `POST /documents/{ts}/test-spec/runs` (or a single Case's run) — never the legacy
 * `/documents/test-run`.
 */
import { reactive } from 'vue'
import { postRequest } from '@shared/api'

import type { TestDocumentView, TestResultRecord } from '../../types/testRun'

const views = reactive(new Map<string, TestDocumentView>())
const reloaders = new Map<string, Set<() => Promise<void> | void>>()

export function publishTestDocumentView(docId: string, view: TestDocumentView | null): void {
  if (view) views.set(docId, view)
  else views.delete(docId)
}

export function testDocumentView(docId: string | null | undefined): TestDocumentView | null {
  return docId ? views.get(docId) ?? null : null
}

export function registerTestDocumentReload(docId: string, reload: () => Promise<void> | void): () => void {
  let set = reloaders.get(docId)
  if (!set) {
    set = new Set()
    reloaders.set(docId, set)
  }
  set.add(reload)
  return () => {
    set?.delete(reload)
    if (set && set.size === 0) reloaders.delete(docId)
  }
}

/** Ask every mounted body showing this document (or reading this TS) to read again. */
export async function reloadTestDocument(docId: string): Promise<void> {
  const targets: Array<() => Promise<void> | void> = []
  for (const [id, set] of reloaders) {
    const view = views.get(id)
    if (id === docId || view?.target_ts === docId) targets.push(...set)
  }
  await Promise.all(targets.map((reload) => reload()))
}

export type SpecRunStateKey =
  | 'queued' | 'preparing' | 'executing' | 'finalizing' | 'cancelling'
  | 'prepare_failed' | 'execution_failed' | 'cancelled'
  | 'stale' | 'unchecked' | 'done' | 'none'

export interface SpecRunState {
  key: SpecRunStateKey
  /** A run is admitted or in flight — nothing else may start one. */
  active: boolean
  runId: string | null
  done: number
  total: number
  overall: string | null
  /** Prepare refusal / execution error code, when the newest run did not produce a result. */
  error: string | null
  tone: 'info' | 'success' | 'danger' | 'warning' | 'neutral'
}

const ACTIVE_PHASES = new Set(['queued', 'preparing', 'executing', 'finalizing'])

function stamp(run: TestResultRecord | null | undefined): string {
  return String(run?.created_at ?? run?.started_at ?? '')
}

/** The one reading of a contract-2 view the pill, the panel and the report all share. */
export function specRunState(view: TestDocumentView | null | undefined): SpecRunState {
  const base = { runId: null, done: 0, total: 0, overall: null, error: null }
  if (!view) return { ...base, key: 'none', active: false, tone: 'neutral' }
  const live = view.active_run ?? null
  if (live) {
    const total = (live.selected_case_ids ?? []).length || (live.case_total ?? 0)
    const done = (live.reported_case_ids ?? []).length
    const phase = String(live.phase ?? 'queued')
    const key: SpecRunStateKey = live.status === 'cancelling'
      ? 'cancelling'
      : (ACTIVE_PHASES.has(phase) ? phase as SpecRunStateKey : 'executing')
    return { ...base, key, active: true, runId: live.run_id ?? null, done, total, tone: 'info' }
  }
  const latest = view.kind === 'TSR' ? (view.report ?? null) : (view.latest_result ?? null)
  const last = view.last_execution ?? null
  if (last && (last.status === 'failed' || last.status === 'cancelled') && stamp(last) >= stamp(latest)) {
    if (last.status === 'cancelled') {
      return { ...base, key: 'cancelled', active: false, runId: last.run_id ?? null, tone: 'warning' }
    }
    const refused = last.prepare_refused?.error ?? null
    return {
      ...base,
      key: refused ? 'prepare_failed' : 'execution_failed',
      active: false,
      runId: last.run_id ?? null,
      error: refused ?? last.error ?? null,
      tone: 'danger',
    }
  }
  const overall = view.effective_result?.summary.overall ?? latest?.overall ?? null
  if (!latest && !view.effective_result) return { ...base, key: 'none', active: false, tone: 'neutral' }
  if (view.basis_valid === false) {
    return { ...base, key: 'stale', active: false, overall, tone: 'warning' }
  }
  if (view.basis_verdict?.state === 'unchecked') {
    return { ...base, key: 'unchecked', active: false, overall, tone: 'neutral' }
  }
  if (!latest) return { ...base, key: 'none', active: false, tone: 'neutral' }
  return {
    ...base,
    key: 'done',
    active: false,
    overall,
    tone: overall === 'PASS' ? 'success' : overall === 'FAIL' ? 'danger' : 'warning',
  }
}

/** [run again] (every automated Case) or one Case's run — the same admission (D#1 §3-2). */
export async function startSpecRun(tsId: string, caseId?: string | null): Promise<{ run_id?: string }> {
  const base = `/api/v1/documents/${encodeURIComponent(tsId)}/test-spec`
  const url = caseId ? `${base}/cases/${encodeURIComponent(caseId)}/run` : `${base}/runs`
  const res = await postRequest<{ run_id?: string }>(url, {})
  return res.data ?? {}
}

export async function cancelSpecRun(runId: string): Promise<void> {
  await postRequest(`/api/v1/documents/test-run/${encodeURIComponent(runId)}/cancel`, {})
}

/** Server refusal code of a failed run/cancel/asset request, for an i18n message. */
export function specErrorCode(error: unknown): string | null {
  const data = (error as { response?: { data?: Record<string, unknown> } })?.response?.data
  const code = data?.error ?? (data?.detail as Record<string, unknown> | undefined)?.error
  return typeof code === 'string' ? code : null
}

const KNOWN_ERRORS = new Set([
  'run_in_progress', 'failure_origin_pending', 'tsr_already_approved', 'doc_not_approved',
  'permission_denied', 'no_automated_bindings', 'case_not_selectable', 'group_disposed',
  'invalid_spec', 'expected_hash_mismatch', 'test_asset_unchanged', 'ts_changed',
  'automated_results_server_only',
])

/** i18n key (under main.test_document.errors) for a refusal; `failed` when unknown. */
export function specErrorKey(error: unknown): string {
  const code = specErrorCode(error)
  return code && KNOWN_ERRORS.has(code) ? code : 'failed'
}
