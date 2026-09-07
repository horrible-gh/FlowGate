-- 103_test_runs_failure_origin.sql
-- flowgate.default.0503 T0009: run-level failure-origin classification evidence.
-- Nullable additive columns; classification is validated by the inbox service, not a CHECK.

ALTER TABLE test_runs ADD COLUMN failure_origin TEXT NULL;
ALTER TABLE test_runs ADD COLUMN failure_origin_reviewer_id TEXT NULL;
ALTER TABLE test_runs ADD COLUMN failure_origin_findings TEXT NULL;
ALTER TABLE test_runs ADD COLUMN failure_origin_comment TEXT NULL;
ALTER TABLE test_runs ADD COLUMN failure_origin_reviewed_at TIMESTAMP NULL;
