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
  code_rework_cycle?: number | null
  port?: number | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  // Present only on the latest run (include_cases=True).
  setup?: TestRunCase[]
  cases?: TestRunCase[]
  teardown?: TestRunCase[]
}
