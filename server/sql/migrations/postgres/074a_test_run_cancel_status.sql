-- 074_test_run_cancel_status.sql
-- flowgate.default.0358 T0004: add 'cancelling'/'cancelled' to test_runs.status so a
-- user-initiated cancel is tracked distinctly from passed/failed and never mistaken for
-- an INFRA failure by the auto-recovery loop (NR0003 상태 어휘와 스키마 결정, option A).
--
-- Postgres names the inline column CHECK from migration 052 'test_runs_status_check'
-- (same default-naming convention confirmed for tokens in migration 064), so drop it and
-- re-add the 5-value list.
ALTER TABLE test_runs DROP CONSTRAINT test_runs_status_check;
ALTER TABLE test_runs ADD CONSTRAINT test_runs_status_check
    CHECK (status IN ('running','passed','failed','cancelling','cancelled'));
