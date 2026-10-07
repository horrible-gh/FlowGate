// Test-run embed shapes, matching test_run / test_run_history in the backend detail
// response (services/test_run_service.py shape_run / _shape_case_item). The latest run
// carries case detail (include_cases=True); history rows omit it.

export interface TestRunCase {
  step_no?: string | null
  kind?: string | null
  case_no?: string | null
  case_title?: string | null
  cmd?: string | null
  expect?: string | null
  result?: 'pass' | 'fail' | 'timeout' | string | null
  exit_code?: number | null
  duration_ms?: number | null
  output_tail?: string | null
  finished_at?: string | null
  // flowgate.default.0503 T0007: native assertion evidence. All three are null for a
  // legacy case with no `assert` field (exit-code-only judging, unchanged).
  assert_mode?: string | null
  actual?: string | null
  comparison_result?: 'match' | 'mismatch' | string | null
}

export interface TestRun {
  run_id?: string | null
  revision_no?: number | null
  status?: 'passed' | 'failed' | 'running' | 'cancelling' | 'cancelled' | string | null
  triggered_via?: string | null
  runner_id?: string | null
  case_total?: number | null
  case_passed?: number | null
  case_failed?: number | null
  error?: string | null
  tsr_doc_id?: string | null
  failure_origin?: 'product_defect' | 'test_defect' | 'hold' | string | null
  failure_origin_comment?: string | null
  failure_origin_provider?: {
    ai_run_id?: string | null
    ai_provider_id?: string | null
    ai_provider_name?: string | null
  } | null
  code_rework_cycle?: number | null
  port?: number | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  // Present only on the latest run (include_cases=True).
  setup?: TestRunCase[]
  cases?: TestRunCase[]
  teardown?: TestRunCase[]
  // flowgate.default.0549 T0008: null for a legacy executable run; 2 for a
  // specification-TS result record, whose verdict is the server-computed `overall`.
  contract_version?: number | null
  overall?: TestVerdict | null
  gate_passed?: boolean
  summary?: TestResultSummary | null
  unmapped?: TestUnmappedResult[]
  conflicts?: TestMappingConflict[]
  source_identity?: Record<string, string>
  basis_id?: string | null
  run_kind?: string | null
}

// ── Specification TS / TSR test report (flowgate.default.0549 T0008) ──────────────

export type TestVerdict = 'PASS' | 'FAIL' | 'BLOCKED' | 'NOT_RUN'
export type TestCategory = 'normal' | 'negative' | 'boundary' | 'regression'
export type TestExecutionMode = 'automated' | 'manual' | 'external'

export interface TestCounts {
  total: number
  pass: number
  fail: number
  blocked: number
  not_run: number
}

export interface TestResultSummary {
  counts?: TestCounts | null
  required_counts?: TestCounts | null
  optional_counts?: TestCounts | null
}

export interface TestEvidence {
  kind: string
  value: string
  label?: string
}

export interface TestSpecCase {
  case_id: string
  title: string
  category: TestCategory | string | null
  requirement: string
  execution_mode: TestExecutionMode | string | null
  required: boolean
  precondition: string
  input: string
  procedure: string
  expected: string
  check_points: string
  automation_ref?: string
  test_assets?: string
}

export interface TestSpecError {
  code: string
  case_id?: string | null
  field?: string | null
  message: string
}

/** One TS case inside a result record (TestRunCase + the spec/result fields). */
export interface TestReportCase extends TestRunCase {
  case_status?: TestVerdict | string | null
  category?: string | null
  requirement?: string | null
  required?: boolean | null
  precondition?: string | null
  input?: string | null
  procedure?: string | null
  check_points?: string | null
  evidence?: TestEvidence[] | null
  defect_ref?: string | null
  execution_mode?: string | null
  checked_by?: string | null
  checked_at?: string | null
  note?: string | null
  source_name?: string | null
  source_identity?: Record<string, string> | null
  result_origin?: string | null
  mapping_conflict?: boolean | null
  result_count?: number | null
  carried_from_run_id?: string | null
}

export interface TestUnmappedResult {
  case_id?: string | null
  status?: string | null
  source_name?: string | null
  actual?: string | null
  origin?: string | null
  reason?: string | null
}

export interface TestMappingConflict {
  case_id: string
  result_count: number
  statuses: string[]
  source_names?: (string | null)[]
}

export interface TestResultRecord extends Omit<TestRun, 'cases'> {
  cases?: TestReportCase[]
}

/** GET /api/v1/documents/{doc_id}/test-document */
export interface TestDocumentView {
  ok?: boolean
  kind: 'TS' | 'TSR' | string
  doc_id: string
  contract_version: number | null
  // TS (contract 2)
  title?: string
  doc_title?: string
  intro?: string
  cases?: TestSpecCase[]
  errors?: TestSpecError[]
  latest_result?: TestResultRecord | null
  doc_review_status?: string | null
  can_start_spec?: boolean
  tsr_doc_id?: string | null
  tsr_review_status?: string | null
  test_basis?: {
    basis_id: string
    source: Record<string, string | null>
    // 0684 T#2: what the run executed (git revision, dirty flag, measured time, run); not part of basis_id.
    binding?: Record<string, unknown> | null
    test_assets: { manifest_hash: string; asset_count: number }
    manifest: { path: string; content_hash: string; role: string }[]
  } | null
  // 0684 T#2: null while the source is unchecked (display paths never measure).
  basis_valid?: boolean | null
  basis_verdict?: { state: 'valid' | 'stale' | 'unverifiable' | 'unchecked'; reasons: string[]; live_basis_id?: string | null } | null
  basis_source?: { kind?: string | null; fingerprint_prefix?: string | null; measured_at?: string | null; captured_at?: string | null; source_dirty?: boolean | null; bundle_id?: string | null; git_revision?: string | null; run_id?: string | null } | null
  case_capabilities?: Record<string, string>
  effective_result?: {
    summary: TestResultSummary & { overall: TestVerdict }
    cases: Array<{ case_id: string; title?: string; expected?: string; actual?: string; required?: boolean; execution_mode?: string; status: TestVerdict; result_origin?: string; evidence?: TestEvidence[] }>
  } | null
  stale_previous_result?: TestResultRecord | null
  active_run?: TestResultRecord | null
  run_history?: TestResultRecord[]
  progress?: { automated_total: number; automated_completed: number }
  source_identity?: Record<string, string | null> | null
  test_asset_identity?: { manifest_hash: string; asset_count: number } | null
  // TSR (contract 2)
  target_ts?: string | null
  report?: TestResultRecord | null
  gate?: { applies: boolean; passed: boolean; overall?: string | null } | null
}
