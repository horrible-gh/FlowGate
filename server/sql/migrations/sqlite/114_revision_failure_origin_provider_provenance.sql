-- 114_revision_failure_origin_provider_provenance.sql
-- Snapshot the effective AI run/provider that produced a document edit revision or
-- a test failure-origin classification. Existing rows deliberately remain NULL.

ALTER TABLE document_revisions ADD COLUMN ai_run_id TEXT;
ALTER TABLE document_revisions ADD COLUMN actual_provider_id TEXT;
ALTER TABLE document_revisions ADD COLUMN actual_provider_name TEXT;

ALTER TABLE test_runs ADD COLUMN failure_origin_ai_run_id TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_actual_provider_id TEXT;
ALTER TABLE test_runs ADD COLUMN failure_origin_actual_provider_name TEXT;
