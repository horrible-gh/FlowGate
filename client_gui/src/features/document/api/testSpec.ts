// flowgate.v02.0048 T#3 — skeleton of the testSpec document API (D#1 §6.3). Owned by T#11 (testSpec), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `testSpec`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   spec       PUT  /api/v1/documents/{doc_id}/test-spec · GET /api/v1/documents/{doc_id}/test-document
//   run        POST /api/v1/documents/{ts_id}/test-spec/runs · …/cases/{case_id}/run
//   result     POST /api/v1/documents/test-results · POST /api/v1/documents/test-run/{run_id}/cancel
//
// flowgate.v02.0048 T#11 — the Inspector test section's calls, all existing v0.1 endpoints (NR0003 §2.7,
// `api/v1/test_run_routes.py`, `documents/routers/documents.py`; no new server API):
//   runs + summary  GET  /api/v1/documents/{doc_id}/test-document -> { ok, kind: TS | TSR, doc_id,
//                   contract_version, … } (test_run_service.describe_test_document). For a contract-2
//                   (specification) TS: cases, errors, latest_result, run_history, active_run,
//                   last_execution, effective_result { summary { overall, counts … } }, progress,
//                   case_capabilities, basis_valid / basis_verdict, tsr_doc_id / tsr_review_status,
//                   doc_review_status. For a TSR: target_ts, report, gate and the same run fields of its
//                   TS. A legacy (contract 1) TS answers its kind and contract only — the v0.1 screen
//                   keeps running it.
//   start           POST /api/v1/documents/{ts_id}/test-spec/runs {} -> 202 { ok, run_id, doc_id, status,
//                   case_total, selected_case_ids, … } — every automated Case (the v0.1 [run again]).
//   cancel          POST /api/v1/documents/test-run/{run_id}/cancel {} -> { ok, run_id, status:
//                   cancelled | cancelling } (idempotent on a finished run).
// A single Case's run (`…/cases/{case_id}/run`), result entry (`POST /documents/test-results`) and the
// spec editor (`PUT …/test-spec`) stay in the v0.1 screen (↗ 열기, D#1 §10.2 T#11 row); they are not
// wrapped. The legacy executable run (`POST /documents/test-run`) is not a test-spec run and is not
// wrapped either.
//
// Refusals keep the server's shape and are thrown as they are; testSpecErrorOf reads them. The run
// routes answer a flat `{ error: <code>, … }`: 403 permission_denied / user_session_required (admin or
// perm_test_run), 404 doc_not_found / run_not_found, 409 run_in_progress / failure_origin_pending /
// doc_not_approved / tsr_already_approved / group_disposed, 422 no_automated_bindings /
// legacy_ts_requires_execution / invalid_spec / no_test_cases / unsupported_contract. The group
// mutation policy answers 423 `{ error: { code: 'GROUP_AI_RUN_LOCKED' } }` before the route while an
// AI run owns the group. `/test-document` refuses with a FastAPI `detail` (404, 403).
import { documentErrorOf } from '../documentError'
import { normalizeApiInput, normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest, putRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

/** Result counts of a record (server `compute_overall` / result meta). */
export interface TestSpecCounts {
  readonly total: number
  readonly pass: number
  readonly fail: number
  readonly blocked: number
  readonly notRun: number
}

/** One Case of a specification TS (the parts the section reads). */
export interface TestSpecCase {
  readonly caseId: string
  readonly title: string | null
  /** `automated` | `manual` | `external` (open set). */
  readonly executionMode: string | null
  readonly required: boolean
}

/** A run / result record (`shape_run`). */
export interface TestSpecRun {
  readonly runId: string
  /** `running` | `cancelling` | `passed` | `failed` | `cancelled`. */
  readonly status: string | null
  /** spec execution phase: `queued` | `preparing` | `executing` | `finalizing` | `finished`. */
  readonly phase: string | null
  /** Server-computed verdict: `PASS` | `FAIL` | `BLOCKED` | `NOT_RUN`. */
  readonly overall: string | null
  /** `spec_execution` (a server run) or a result record. */
  readonly runKind: string | null
  readonly caseTotal: number | null
  readonly selectedCaseIds?: readonly string[]
  readonly passedCases?: number
  readonly selectedCases: number
  readonly reportedCases: number
  readonly counts: TestSpecCounts | null
  /** Execution error code, when the run did not produce a result. */
  readonly error: string | null
  /** Why the run was refused while it prepared, when it was. */
  readonly prepareRefused: string | null
  readonly tsrDocId: string | null
  readonly startedAt: string | null
  readonly finishedAt: string | null
  readonly createdAt: string | null
}

/** The TS / TSR test view of one document (`GET /documents/{doc_id}/test-document`). */
export interface TestSpecView {
  readonly docId: string
  /** `TS` | `TSR` | another type (no test view). */
  readonly kind: string
  /** `test_contract_version`: 1 = legacy executable TS, 2 = specification TS. */
  readonly contractVersion: number | null
  /** The TS the runs belong to: the document itself (TS) or its target (TSR). */
  readonly tsDocId: string | null
  /** The TS's review status (a TSR reads it from its TS). */
  readonly tsReviewStatus: string | null
  /** The report the TS feeds (TS) or the document itself (TSR). */
  readonly tsrDocId: string | null
  readonly tsrReviewStatus: string | null
  readonly cases: readonly TestSpecCase[]
  /** Case id -> what the server runner can do with it (`case_selectable` | `suite_only` | …). */
  readonly caseCapabilities: Readonly<Record<string, string>>
  readonly specErrors: number
  /** Full editor data from the same response; existing summary consumers need not use it. */
  readonly details?: {
    readonly complete: boolean
    readonly title: string | null
    readonly intro: string | null
    readonly cases: readonly TestSpecCaseDetail[]
    readonly errors: readonly TestSpecFormatError[]
    readonly rows: readonly TestSpecRecordRow[]
    readonly storedRows: readonly TestSpecRecordRow[]
  }
  /** TS: the newest result record; TSR: the record the report was assembled from. */
  readonly latest: TestSpecRun | null
  readonly activeRun: TestSpecRun | null
  readonly lastExecution: TestSpecRun | null
  /** Newest first (the server keeps the last 30). */
  readonly history: readonly TestSpecRun[]
  /** The valid result on the live source: its verdict and counts. */
  readonly effective: { readonly overall: string | null; readonly counts: TestSpecCounts | null } | null
  /** false: the newest result is stale for the live source; null: not judged (unchecked). */
  readonly basisValid: boolean | null
  readonly basisState: string | null
  readonly progress: { readonly automatedTotal: number; readonly automatedCompleted: number } | null
  /** TSR only: the test gate. */
  readonly gate: { readonly applies: boolean; readonly passed: boolean; readonly overall: string | null } | null
}

function toTestSpecCounts(raw: unknown): TestSpecCounts | null {
  const row = record(raw)
  const total = whole(row.total)
  if (total === null) return null
  return { total, pass: whole(row.pass) ?? 0, fail: whole(row.fail) ?? 0, blocked: whole(row.blocked) ?? 0, notRun: whole(row.not_run) ?? 0 }
}

/** One run / result record; null without a run_id. */
export function toTestSpecRun(raw: unknown): TestSpecRun | null {
  const row = record(raw)
  const runId = text(row.run_id)
  if (runId === null) return null
  return {
    runId,
    status: text(row.status),
    phase: text(row.phase),
    overall: text(row.overall),
    runKind: text(row.run_kind),
    caseTotal: whole(row.case_total),
    selectedCaseIds: list(row.selected_case_ids).filter((id): id is string => typeof id === 'string'),
    passedCases: whole(row.case_passed) ?? toTestSpecCounts(record(row.summary).counts)?.pass ?? 0,
    selectedCases: list(row.selected_case_ids).length,
    reportedCases: list(row.reported_case_ids).length,
    counts: toTestSpecCounts(record(row.summary).counts),
    error: text(row.error),
    prepareRefused: text(record(row.prepare_refused).error),
    tsrDocId: text(row.tsr_doc_id),
    startedAt: text(row.started_at),
    finishedAt: text(row.finished_at),
    createdAt: text(row.created_at),
  }
}

function toTestSpecCase(raw: unknown): TestSpecCase | null {
  const row = record(raw)
  const caseId = text(row.case_id)
  if (caseId === null) return null
  return { caseId, title: text(row.title), executionMode: text(row.execution_mode), required: row.required !== false }
}

/** The `/test-document` response -> TestSpecView. Throws when it carries no doc_id. */
export function toTestSpecView(raw: unknown): TestSpecView {
  const data = record(raw)
  const docId = text(data.doc_id)
  if (docId === null) throw new Error('test-document response without doc_id')
  const kind = (text(data.kind) ?? '').toUpperCase()
  const tsr = kind === 'TSR'
  const effective = record(data.effective_result)
  const summary = record(effective.summary)
  const verdict = record(data.basis_verdict)
  const progress = record(data.progress)
  const gate = record(data.gate)
  const capabilities = record(data.case_capabilities)
  return {
    docId,
    kind,
    contractVersion: whole(data.contract_version),
    tsDocId: tsr ? text(data.target_ts) : kind === 'TS' ? docId : null,
    tsReviewStatus: text(tsr ? data.ts_review_status : data.doc_review_status),
    tsrDocId: tsr ? docId : text(data.tsr_doc_id),
    tsrReviewStatus: text(tsr ? data.doc_review_status : data.tsr_review_status),
    cases: list(data.cases).flatMap((item) => toTestSpecCase(item) ?? []),
    caseCapabilities: Object.fromEntries(Object.entries(capabilities).flatMap(([id, value]) => (text(value) ? [[id, value as string]] : []))),
    specErrors: list(data.errors).length,
    details: {
      complete: whole(data.contract_version) !== 2 || Array.isArray(data.cases),
      title: text(data.title) ?? text(data.doc_title), intro: written(data.intro),
      cases: list(data.cases).flatMap((item) => toTestSpecCaseDetail(item) ?? []),
      errors: list(data.errors).map((item) => {
        const error = record(item)
        return { code: text(error.code), caseId: text(error.case_id), field: text(error.field), message: text(error.message) }
      }),
      rows: list(data.effective_result ? effective.cases : record(tsr ? data.report : data.latest_result).cases).flatMap((item) => toTestSpecResultRow(item) ?? []),
      storedRows: list(record(tsr ? data.report : data.latest_result).cases).flatMap((item) => toTestSpecResultRow(item) ?? []),
    },
    latest: toTestSpecRun(tsr ? data.report : data.latest_result),
    activeRun: toTestSpecRun(data.active_run),
    lastExecution: toTestSpecRun(data.last_execution),
    history: list(data.run_history).flatMap((item) => toTestSpecRun(item) ?? []),
    effective: 'effective_result' in data && data.effective_result
      ? { overall: text(summary.overall), counts: toTestSpecCounts(summary.counts) }
      : null,
    basisValid: typeof data.basis_valid === 'boolean' ? data.basis_valid : null,
    basisState: text(verdict.state),
    progress: whole(progress.automated_total) === null
      ? null
      : { automatedTotal: whole(progress.automated_total) ?? 0, automatedCompleted: whole(progress.automated_completed) ?? 0 },
    gate: tsr && 'gate' in data && data.gate
      ? { applies: gate.applies === true, passed: gate.passed === true, overall: text(gate.overall) }
      : null,
  }
}

/** Reads the test view (runs, summary) of a TS or TSR. Transport errors are thrown as they are. */
export async function testSpecRead(docId: string): Promise<TestSpecView> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/test-document`)
  return toTestSpecView(response.data)
}

/** What a start or a cancel answered. */
export interface TestSpecRunAck {
  readonly runId: string | null
  readonly status: string | null
}

export function toTestSpecRunAck(raw: unknown): TestSpecRunAck {
  const row = record(raw)
  return { runId: text(row.run_id), status: text(row.status) }
}

/** Starts a server run of every automated Case of a specification TS. */
export async function testSpecRunStart(tsDocId: string): Promise<TestSpecRunAck> {
  const response = await postRequest<unknown>(`${API}/documents/${encodeURIComponent(tsDocId)}/test-spec/runs`, {})
  return toTestSpecRunAck(response.data)
}

/** Cancels a run (a finished one answers its final status). */
export async function testSpecRunCancel(runId: string): Promise<TestSpecRunAck> {
  const response = await postRequest<unknown>(`${API}/documents/test-run/${encodeURIComponent(runId)}/cancel`, {})
  return toTestSpecRunAck(response.data)
}

/**
 * Why a test call was refused:
 * forbidden = 403 (no perm_test_run, not admin, or not a user session); running = a run of the TS is
 * in flight (409 run_in_progress) or an AI run owns the group (423); disposed = the group was disposed;
 * not_approved = the TS is not approved (409 doc_not_approved); report_approved = its report is approved
 * (409 tsr_already_approved); origin_pending = a FAIL waits for its origin review (409
 * failure_origin_pending); no_automated = no automated Case the server can run (422
 * no_automated_bindings); conflict = another 409; invalid = another 400 / 422; not_found 404;
 * network = no answer.
 */
export type TestSpecErrorKind =
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

export interface TestSpecError {
  readonly kind: TestSpecErrorKind
  readonly status: number | null
  readonly code: string | null
}

const KIND_BY_CODE: Readonly<Record<string, TestSpecErrorKind>> = {
  permission_denied: 'forbidden',
  user_session_required: 'forbidden',
  run_in_progress: 'running',
  GROUP_AI_RUN_LOCKED: 'running',
  group_disposed: 'disposed',
  doc_not_approved: 'not_approved',
  tsr_already_approved: 'report_approved',
  failure_origin_pending: 'origin_pending',
  no_automated_bindings: 'no_automated',
}

/** What a failed test call threw, as kind / status / code. Only code and status are read. */
export function testSpecErrorOf(error: unknown): TestSpecError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  // The run routes answer a flat `{ error: <code> }`; the mutation policy an `{ error: { code } }` envelope.
  const code = normalized.code ?? text(data.error)?.trim() ?? text(record(data.detail).error)?.trim() ?? null
  const status = normalized.status
  let kind: TestSpecErrorKind
  if (normalized.network) kind = 'network'
  else if (code !== null && KIND_BY_CODE[code]) kind = KIND_BY_CODE[code]
  else if (status === 423) kind = 'running'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 409) kind = 'conflict'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code: code || null }
}

/** Save refusals use the existing endpoint classifiers, with the content endpoint's detail contracts. */
export function testSpecMutationErrorOf(error: unknown, rawSave = false) {
  const parsed = rawSave ? documentErrorOf(error) : testSpecErrorOf(error)
  const data = record(normalizeApiInput(error).data)
  const detail = record(data.detail)
  const body = Object.keys(detail).length ? detail : data
  const code = parsed.code ?? text(body.error) ?? text(body.code)
  const detailText = text(data.detail) ?? ''
  let kind: string = parsed.kind
  if (['GROUP_AI_RUN_LOCKED', 'run_in_progress', 'group_ai_running'].includes(code ?? '') || parsed.status === 423 || detailText.startsWith('group_ai_running')) kind = 'running'
  else if (code === 'group_disposed' || detailText.startsWith('group_disposed') || detailText === 'Modification not allowed: the group has been disposed.') kind = 'disposed'
  else if (detailText.startsWith('Modification not allowed after final approval.') || detailText.startsWith('Modification not allowed for status:') || ['document_not_editable', 'not_editable', 'doc_not_editable'].includes(code ?? '') || (rawSave && parsed.status === 422 && code !== 'invalid_spec')) kind = 'approved'
  else if (code === 'invalid_spec') kind = 'invalid'
  const fieldErrors: TestSpecFormatError[] = list(body.errors).map((item) => {
    const row = record(item)
    return { code: text(row.code), caseId: text(row.case_id), field: text(row.field), message: text(row.message) }
  })
  return { kind, status: parsed.status, code, message: text(body.message) ?? text(data.detail), fieldErrors }
}

// flowgate.v02.0053 T#4 — the document window's scenario / report views (the v0.1 TestDocumentBody's
// TestSpecPanel and TestReportPanel, read only) need the Cases' written fields and the report rows of the
// same `/test-document` answer. Same endpoint, same read; TestSpecView is left as it is.

/** The written fields of a Case (null when empty). */
export interface TestSpecCaseFields {
  readonly requirement: string | null
  readonly precondition: string | null
  readonly input: string | null
  readonly procedure: string | null
  readonly expected: string | null
  readonly checkPoints: string | null
  readonly automationRef?: string | null
  /** The automated Case's execution command and working directory (any language or framework). */
  readonly runCommand?: string | null
  readonly runCwd?: string | null
  readonly testAssets?: string | null
}

/** A Case of a specification TS as the scenario view shows it. */
export interface TestSpecCaseDetail extends TestSpecCase {
  /** `normal` | `negative` | `boundary` | `regression` (open set). */
  readonly category: string | null
  readonly fields: TestSpecCaseFields
}

export interface TestSpecEvidence {
  readonly kind: string | null
  readonly value: string
  readonly label: string | null
}

/** One Case of the result: the valid result on the live source first, else the stored record's row. */
export interface TestSpecResultRow {
  readonly caseId: string
  readonly title: string | null
  /** `PASS` | `FAIL` | `BLOCKED` | `NOT_RUN`. */
  readonly status: string | null
  readonly required: boolean
  readonly executionMode: string | null
  readonly expected: string | null
  readonly actual: string | null
  readonly evidence: readonly TestSpecEvidence[]
  readonly defectRef: string | null
}

/** A row as the record holds it: also who checked the Case and when (the report text writes them, test_spec_service normalize_results: any text). */
export interface TestSpecRecordRow extends TestSpecResultRow {
  readonly checkedBy: string | null
  readonly checkedAt: string | null
}

/** The TS / TSR as the document window shows it. */
export interface TestSpecDocument {
  readonly view: TestSpecView
  /** The specification's own title and intro paragraph. */
  readonly title: string | null
  readonly intro: string | null
  readonly cases: readonly TestSpecCaseDetail[]
  readonly errors: readonly TestSpecFormatError[]
  readonly rows: readonly TestSpecRecordRow[]
  /** The rows of the stored record itself (a TSR's report run, a TS's latest result), whatever the valid result is. */
  readonly storedRows: readonly TestSpecRecordRow[]
  /** Overall verdict of the result (valid result first, else the stored record). */
  readonly overall: string | null
  readonly counts: TestSpecCounts | null
  readonly requiredCounts: TestSpecCounts | null
  /** Cases the live run has not reported yet. */
  readonly pendingCaseIds: readonly string[]
  readonly unmapped: number
  readonly conflicts: number
}

const written = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null)

function toTestSpecEvidence(raw: unknown): TestSpecEvidence | null {
  const row = record(raw)
  const value = written(row.value)
  return value === null ? null : { kind: text(row.kind), value, label: text(row.label) }
}

function toTestSpecCaseDetail(raw: unknown): TestSpecCaseDetail | null {
  const base = toTestSpecCase(raw)
  if (base === null) return null
  const row = record(raw)
  return {
    ...base,
    category: text(row.category),
    fields: {
      requirement: written(row.requirement),
      precondition: written(row.precondition),
      input: written(row.input),
      procedure: written(row.procedure),
      expected: written(row.expected),
      checkPoints: written(row.check_points),
      automationRef: written(row.automation_ref),
      runCommand: written(row.run_command),
      runCwd: written(row.run_cwd),
      testAssets: written(row.test_assets),
    },
  }
}

/** A row of `effective_result.cases` (case_id / status / expected) or of a stored record (case_no / case_status / expect). */
function toTestSpecResultRow(raw: unknown): TestSpecRecordRow | null {
  const row = record(raw)
  const caseId = text(row.case_id) ?? text(row.case_no)
  if (caseId === null) return null
  return {
    caseId,
    title: text(row.title) ?? text(row.case_title),
    status: text(row.status) ?? text(row.case_status),
    required: row.required !== false,
    executionMode: text(row.execution_mode),
    expected: written(row.expected) ?? written(row.expect),
    actual: written(row.actual),
    evidence: list(row.evidence).flatMap((item) => toTestSpecEvidence(item) ?? []),
    defectRef: text(row.defect_ref),
    checkedBy: text(row.checked_by),
    checkedAt: text(row.checked_at),
  }
}

export function toTestSpecDocument(raw: unknown): TestSpecDocument {
  const view = toTestSpecView(raw)
  const data = record(raw)
  const stored = record(view.kind === 'TSR' ? data.report : data.latest_result)
  const effective = data.effective_result ? record(data.effective_result) : null
  const summary = record(effective ? effective.summary : stored.summary)
  const rows = list(effective ? effective.cases : stored.cases).flatMap((item) => toTestSpecResultRow(item) ?? [])
  return {
    view,
    title: text(data.title) ?? text(data.doc_title),
    intro: written(data.intro),
    cases: list(data.cases).flatMap((item) => toTestSpecCaseDetail(item) ?? []),
    errors: view.details?.errors ?? [],
    rows,
    storedRows: list(stored.cases).flatMap((item) => toTestSpecResultRow(item) ?? []),
    overall: text(summary.overall) ?? text(stored.overall),
    counts: toTestSpecCounts(summary.counts),
    requiredCounts: toTestSpecCounts(summary.required_counts),
    pendingCaseIds: data.active_run ? list(data.pending_case_ids).flatMap((id) => (text(id) ? [id as string] : [])) : [],
    unmapped: list(stored.unmapped).length,
    conflicts: list(stored.conflicts).length,
  }
}

/** Reads a TS / TSR for the document window (`GET /documents/{doc_id}/test-document`, as testSpecRead). */
export async function testSpecDocumentRead(docId: string): Promise<TestSpecDocument> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/test-document`)
  return toTestSpecDocument(response.data)
}


export interface TestSpecFormatError {
  readonly code: string | null
  readonly caseId: string | null
  readonly field: string | null
  readonly message: string | null
}

/** The existing structured editor contract. No client copy of trailing prose is sent. */
export interface TestSpecCasePayload {
  readonly case_id: string
  readonly title: string
  readonly category: string | null
  readonly requirement: string | null
  readonly execution_mode: string | null
  readonly required: boolean
  readonly precondition: string | null
  readonly input: string | null
  readonly procedure: string | null
  readonly expected: string | null
  readonly check_points: string | null
  readonly automation_ref: string | null
  /** Sent only when written, so a case without an execution command keeps its payload shape. */
  readonly run_command?: string | null
  readonly run_cwd?: string | null
  readonly test_assets: string | null
}

export function testSpecCasePayload(item: TestSpecCaseDetail): TestSpecCasePayload {
  return { case_id: item.caseId, title: item.title ?? '', category: item.category,
    requirement: item.fields.requirement, execution_mode: item.executionMode, required: item.required,
    precondition: item.fields.precondition, input: item.fields.input, procedure: item.fields.procedure,
    expected: item.fields.expected, check_points: item.fields.checkPoints,
    automation_ref: item.fields.automationRef ?? null, test_assets: item.fields.testAssets ?? null,
    ...(item.fields.runCommand ? { run_command: item.fields.runCommand } : {}),
    ...(item.fields.runCwd ? { run_cwd: item.fields.runCwd } : {}) }
}

export async function testBriefRead(docId: string): Promise<{ text: string; content: string }> {
  const response = await getRequest<{ text: string; content: string }>(`${API}/documents/${encodeURIComponent(docId)}/test-brief`)
  return response.data
}
export async function testBriefSave(docId: string, text: string, expectedContent: string): Promise<{ text: string; content: string }> {
  const response = await putRequest<{ text: string; content: string }>(`${API}/documents/${encodeURIComponent(docId)}/test-brief`, { text, expected_content: expectedContent })
  return response.data
}

export async function testSpecSave(docId: string, cases: readonly TestSpecCasePayload[], title: string | null): Promise<void> {
  await putRequest(`${API}/documents/${encodeURIComponent(docId)}/test-spec`, { cases, ...(title ? { title } : {}) })
}

export async function testSpecCaseRun(tsDocId: string, caseId: string): Promise<TestSpecRunAck> {
  const response = await postRequest<unknown>(`${API}/documents/${encodeURIComponent(tsDocId)}/test-spec/cases/${encodeURIComponent(caseId)}/run`, {})
  return toTestSpecRunAck(response.data)
}

export interface TestSpecResultEntry {
  readonly case_id: string
  readonly status: 'PASS' | 'FAIL' | 'BLOCKED' | 'NOT_RUN'
  readonly actual?: string
  readonly evidence?: readonly TestSpecEvidence[]
}

/** A partial rejection is a refusal too; never show it as a successful result entry. */
export async function testSpecResultEnter(docId: string, result: TestSpecResultEntry): Promise<void> {
  const response = await postRequest<unknown>(`${API}/documents/test-results`, { doc_id: docId, results: [result] })
  if (list(record(response.data).refused_results).length) {
    throw { response: { status: 422, data: { error: 'invalid_spec', refused_results: record(response.data).refused_results } } }
  }
}
