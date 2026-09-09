-- 103_test_runs_failure_origin.sql
-- flowgate.default.0503 T0009: run-level failure-origin classification evidence.
-- Nullable additive columns; classification is validated by the inbox service, not a CHECK.

ALTER TABLE test_runs ADD COLUMN failure_origin TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_reviewer_id TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_findings TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_comment TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_reviewed_at TIMESTAMP;
