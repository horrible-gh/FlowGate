-- 114_revision_failure_origin_provider_provenance.sql
-- Snapshot the effective AI run/provider that produced a test failure-origin
-- classification. Existing rows deliberately remain NULL.

ALTER TABLE test_runs ADD COLUMN failure_origin_ai_run_id TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_actual_provider_id TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_actual_provider_name TEXT;
