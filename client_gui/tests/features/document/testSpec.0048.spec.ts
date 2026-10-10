/**
 * flowgate.v02.0048 T#11 — the test section's calls in features/document/api/testSpec.ts (D#1 §6.3, §12.1;
 * NR0003 §2.7): the requests sent to the existing v0.1 endpoints (GET /documents/{doc_id}/test-document,
 * POST /documents/{ts_id}/test-spec/runs, POST /documents/test-run/{run_id}/cancel), the server's
 * snake_case answers (test_run_service.describe_test_document / shape_run) -> camelCase for a
 * specification TS, its TSR and a legacy TS, and how a refusal is read — the run routes' flat
 * `{ error: <code> }`, the mutation policy envelope (423), FastAPI `detail`, no answer.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
  patchRequest: vi.fn(),
}))

import {
  testSpecErrorOf,
  testSpecRead,
  testSpecRunCancel,
  testSpecRunStart,
  toTestSpecRun,
  toTestSpecView,
  type TestSpecErrorKind,
} from '@/features/document'

const TS = 'flowgate.v02.0048.0031-TS'
const TSR = 'flowgate.v02.0048.0032-TSR'

const counts = (pass: number, fail: number, blocked = 0, notRun = 0) => ({
  total: pass + fail + blocked + notRun,
  pass,
  fail,
  blocked,
  not_run: notRun,
})

/** A shape_run record of a spec execution (include_cases=False). */
const runRow = (runId: string, fields: Record<string, unknown> = {}) => ({
  run_id: runId,
  revision_no: 0,
  status: 'passed',
  triggered_via: 'ui',
  runner_id: 'u1',
  case_total: 3,
  case_passed: 3,
  case_failed: 0,
  error: null,
  tsr_doc_id: TSR,
  started_at: '2026-10-09T10:00:00Z',
  finished_at: '2026-10-09T10:02:00Z',
  created_at: '2026-10-09T10:00:00Z',
  contract_version: 2,
  overall: 'PASS',
  gate_passed: true,
  summary: { counts: counts(3, 0), required_counts: counts(2, 0), optional_counts: counts(1, 0) },
  run_kind: 'spec_execution',
  phase: 'finished',
  admission: 'request',
  selected_case_ids: ['TC-1', 'TC-2'],
  reported_case_ids: ['TC-1', 'TC-2'],
  prepare_refused: null,
  ...fields,
})

/** describe_test_document of a contract-2 TS with a live run. */
const tsDocument = () => ({
  ok: true,
  kind: 'TS',
  doc_id: TS,
  contract_version: 2,
  doc_title: '정렬 테스트',
  title: '정렬 테스트',
  intro: '',
  cases: [
    { case_id: 'TC-1', title: '오름차순', category: 'normal', execution_mode: 'automated', required: true },
    { case_id: 'TC-2', title: '빈 목록', category: 'boundary', execution_mode: 'automated', required: true },
    { case_id: 'TC-3', title: '화면 확인', category: 'normal', execution_mode: 'manual', required: false },
    { title: 'id 없음', execution_mode: 'automated' },
  ],
  errors: [],
  latest_result: runRow('tr_old'),
  doc_review_status: 'approved',
  tsr_doc_id: TSR,
  tsr_review_status: 'pending_review',
  run_history: [runRow('tr_live', { status: 'running', phase: 'executing', overall: null, reported_case_ids: ['TC-1'] }), runRow('tr_old'), { status: 'passed' }],
  last_execution: runRow('tr_live', { status: 'running', phase: 'executing' }),
  result_entry_case_ids: ['TC-3'],
  active_run: runRow('tr_live', { status: 'running', phase: 'executing', overall: null, reported_case_ids: ['TC-1'] }),
  pending_case_ids: ['TC-2'],
  case_capabilities: { 'TC-1': 'case_selectable', 'TC-2': 'suite_only', 'TC-3': 'none', 'TC-x': 7 },
  test_basis: { basis_id: 'b1' },
  basis_valid: true,
  basis_verdict: { state: 'valid', reasons: [], live_basis_id: 'b1' },
  effective_run_id: 'tr_old',
  effective_result: { summary: { overall: 'PASS', counts: counts(2, 0, 0, 1) }, cases: [] },
  progress: { automated_total: 2, automated_completed: 1 },
})

/** describe_test_document of the TSR assembled from that TS. */
const tsrDocument = () => ({
  ok: true,
  kind: 'TSR',
  doc_id: TSR,
  contract_version: 2,
  target_ts: TS,
  report: runRow('tr_old', { status: 'failed', overall: 'FAIL', summary: { counts: counts(1, 1) } }),
  gate: { applies: true, passed: false, overall: 'FAIL', run_id: 'tr_old' },
  doc_review_status: 'pending_review',
  run_history: [runRow('tr_old', { overall: 'FAIL' })],
  active_run: null,
  last_execution: runRow('tr_old', { status: 'failed' }),
  basis_valid: false,
  basis_verdict: { state: 'stale', reasons: ['source_changed'] },
  ts_review_status: 'approved',
})

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('testSpecRead — GET /documents/{doc_id}/test-document', () => {
  it('reads a specification TS: Cases, capabilities, the live run, history, the valid result and the report it feeds', async () => {
    getRequest.mockResolvedValue({ data: tsDocument() })
    const view = await testSpecRead(TS)
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(TS)}/test-document`)
    expect(view).toMatchObject({
      docId: TS,
      kind: 'TS',
      contractVersion: 2,
      tsDocId: TS,
      tsReviewStatus: 'approved',
      tsrDocId: TSR,
      tsrReviewStatus: 'pending_review',
      specErrors: 0,
      basisValid: true,
      basisState: 'valid',
      progress: { automatedTotal: 2, automatedCompleted: 1 },
      effective: { overall: 'PASS', counts: { total: 3, pass: 2, fail: 0, blocked: 0, notRun: 1 } },
      gate: null,
    })
    // A Case without an id cannot be counted against a capability; it is dropped.
    expect(view.cases).toEqual([
      { caseId: 'TC-1', title: '오름차순', executionMode: 'automated', required: true },
      { caseId: 'TC-2', title: '빈 목록', executionMode: 'automated', required: true },
      { caseId: 'TC-3', title: '화면 확인', executionMode: 'manual', required: false },
    ])
    expect(view.caseCapabilities).toEqual({ 'TC-1': 'case_selectable', 'TC-2': 'suite_only', 'TC-3': 'none' })
    expect(view.activeRun).toMatchObject({ runId: 'tr_live', status: 'running', phase: 'executing', selectedCases: 2, reportedCases: 1, overall: null })
    expect(view.latest).toMatchObject({ runId: 'tr_old', overall: 'PASS', counts: { total: 3, pass: 3 }, tsrDocId: TSR })
    // A history row without run_id is dropped.
    expect(view.history.map((run) => run.runId)).toEqual(['tr_live', 'tr_old'])
  })

  it('reads a TSR: its TS, the report record, the gate and the TS review state', async () => {
    getRequest.mockResolvedValue({ data: tsrDocument() })
    const view = await testSpecRead(TSR)
    expect(view).toMatchObject({
      docId: TSR,
      kind: 'TSR',
      contractVersion: 2,
      tsDocId: TS,
      tsReviewStatus: 'approved',
      tsrDocId: TSR,
      tsrReviewStatus: 'pending_review',
      latest: { runId: 'tr_old', overall: 'FAIL', status: 'failed', counts: { pass: 1, fail: 1 } },
      lastExecution: { runId: 'tr_old', status: 'failed' },
      activeRun: null,
      gate: { applies: true, passed: false, overall: 'FAIL' },
      basisValid: false,
      basisState: 'stale',
      effective: null,
      cases: [],
    })
  })

  it('a legacy executable TS answers its kind and contract only', () => {
    expect(toTestSpecView({ ok: true, kind: 'TS', doc_id: TS, contract_version: 1, doc_title: 't', can_start_spec: false })).toMatchObject({
      kind: 'TS',
      contractVersion: 1,
      tsDocId: TS,
      cases: [],
      history: [],
      activeRun: null,
      latest: null,
      effective: null,
      progress: null,
    })
  })

  it('a TSR assembled from a legacy run has no TS run fields; an answer without doc_id is refused', () => {
    expect(toTestSpecView({ ok: true, kind: 'TSR', doc_id: TSR, contract_version: 1, target_ts: TS })).toMatchObject({
      kind: 'TSR',
      contractVersion: 1,
      tsDocId: TS,
      gate: null,
    })
    expect(() => toTestSpecView({ ok: true, kind: 'TS' })).toThrow(/doc_id/)
  })

  it('a prepare refusal and an execution error are kept apart', () => {
    expect(toTestSpecRun(runRow('r1', { status: 'failed', prepare_refused: { error: 'basis_capture_failed', reasons: 'x' } }))).toMatchObject({
      prepareRefused: 'basis_capture_failed',
      error: null,
    })
    expect(toTestSpecRun(runRow('r2', { status: 'failed', error: 'setup_failed' }))).toMatchObject({ prepareRefused: null, error: 'setup_failed' })
    expect(toTestSpecRun({ status: 'running' })).toBeNull()
  })

  it('transport errors are thrown as they are', async () => {
    const refusal = { isAxiosError: true, response: { status: 404, data: { detail: 'Document not found: x' } } }
    getRequest.mockRejectedValue(refusal)
    await expect(testSpecRead(TS)).rejects.toBe(refusal)
  })
})

describe('testSpecRunStart / testSpecRunCancel', () => {
  it('starts every automated Case of the TS through POST /documents/{ts_id}/test-spec/runs', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'tr_new', doc_id: TS, status: 'running', case_total: 2, selected_case_ids: ['TC-1', 'TC-2'] } })
    await expect(testSpecRunStart(TS)).resolves.toEqual({ runId: 'tr_new', status: 'running' })
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(TS)}/test-spec/runs`, {})
  })

  it('cancels a run through POST /documents/test-run/{run_id}/cancel', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'tr_live', status: 'cancelling' } })
    await expect(testSpecRunCancel('tr_live')).resolves.toEqual({ runId: 'tr_live', status: 'cancelling' })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run/tr_live/cancel', {})
  })

  it('refusals are thrown as they are', async () => {
    const refusal = { isAxiosError: true, response: { status: 409, data: { error: 'run_in_progress', run_id: 'tr_live' } } }
    postRequest.mockRejectedValue(refusal)
    await expect(testSpecRunStart(TS)).rejects.toBe(refusal)
    await expect(testSpecRunCancel('tr_live')).rejects.toBe(refusal)
  })
})

describe('testSpecErrorOf — the run routes, the mutation policy and FastAPI refusals', () => {
  const flat = (status: number, error: string) => ({ isAxiosError: true, response: { status, data: { error, doc_id: TS } } })
  it.each<[string, unknown, TestSpecErrorKind, number | null, string | null]>([
    ['403 permission_denied', flat(403, 'permission_denied'), 'forbidden', 403, 'permission_denied'],
    ['403 user_session_required', flat(403, 'user_session_required'), 'forbidden', 403, 'user_session_required'],
    ['409 run_in_progress', flat(409, 'run_in_progress'), 'running', 409, 'run_in_progress'],
    ['423 GROUP_AI_RUN_LOCKED', { isAxiosError: true, response: { status: 423, data: { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm' } } } }, 'running', 423, 'GROUP_AI_RUN_LOCKED'],
    ['409 group_disposed', flat(409, 'group_disposed'), 'disposed', 409, 'group_disposed'],
    ['409 doc_not_approved', flat(409, 'doc_not_approved'), 'not_approved', 409, 'doc_not_approved'],
    ['409 tsr_already_approved', flat(409, 'tsr_already_approved'), 'report_approved', 409, 'tsr_already_approved'],
    ['409 failure_origin_pending', flat(409, 'failure_origin_pending'), 'origin_pending', 409, 'failure_origin_pending'],
    ['422 no_automated_bindings', flat(422, 'no_automated_bindings'), 'no_automated', 422, 'no_automated_bindings'],
    ['422 legacy_ts_requires_execution', flat(422, 'legacy_ts_requires_execution'), 'invalid', 422, 'legacy_ts_requires_execution'],
    ['409 another code', flat(409, 'something_new'), 'conflict', 409, 'something_new'],
    ['404 run_not_found', flat(404, 'run_not_found'), 'not_found', 404, 'run_not_found'],
    ['404 FastAPI detail string', { isAxiosError: true, response: { status: 404, data: { detail: 'Document not found: x' } } }, 'not_found', 404, null],
    ['403 FastAPI detail string', { isAxiosError: true, response: { status: 403, data: { detail: 'Permission denied' } } }, 'forbidden', 403, null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null],
    ['500', { isAxiosError: true, response: { status: 500, data: { error: 'internal_error' } } }, 'other', 500, 'internal_error'],
    ['something thrown', new Error('boom'), 'other', null, null],
  ])('%s', (_label, error, kind, status, code) => {
    expect(testSpecErrorOf(error)).toEqual({ kind, status, code })
  })
})
