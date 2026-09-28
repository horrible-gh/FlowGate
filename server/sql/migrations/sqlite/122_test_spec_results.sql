-- 122_test_spec_results.sql
-- flowgate.default.0549 T0008: specification TS (test_contract_version 2) and the TSR test report.
--
-- test_runs.contract_version  -- NULL for every legacy executable run (unchanged); 2 for a
--                                specification-TS result record, which is born terminal and
--                                never picked by TestRunWorker.
-- test_runs.overall           -- server-computed verdict from required cases:
--                                PASS / FAIL / BLOCKED / NOT_RUN (NULL for legacy runs).
-- test_runs.result_meta       -- JSON: counts, unmapped results, mapping conflicts, submission
--                                source identity, and the chain continuation context.
-- test_run_cases.case_status  -- per-TS-Case-ID verdict PASS/FAIL/BLOCKED/NOT_RUN (legacy result
--                                column keeps its pass/fail/timeout CHECK untouched).
-- test_run_cases.case_meta    -- JSON: category, requirement, required, execution mode, evidence,
--                                defect/reference, checked_by/at, source identity, carry-over.
--
-- All nullable and additive: rows predating this migration keep NULL and read exactly as
-- before. No CHECK, no index (read by primary key / doc_id alongside the existing columns).

BEGIN;

ALTER TABLE test_runs ADD COLUMN contract_version INTEGER;
ALTER TABLE test_runs ADD COLUMN overall TEXT;
ALTER TABLE test_runs ADD COLUMN result_meta TEXT;
ALTER TABLE test_run_cases ADD COLUMN case_status TEXT;
ALTER TABLE test_run_cases ADD COLUMN case_meta TEXT;

COMMIT;
