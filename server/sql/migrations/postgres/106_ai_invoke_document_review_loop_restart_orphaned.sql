-- 106_ai_invoke_document_review_loop_restart_orphaned.sql
-- flowgate.default.0486 T0019: widen the durable loop terminal reason for restart recovery.
BEGIN;
ALTER TABLE ai_invoke_document_review_loops
  DROP CONSTRAINT ai_invoke_document_review_loops_stop_reason_check;
ALTER TABLE ai_invoke_document_review_loops
  ADD CONSTRAINT ai_invoke_document_review_loops_stop_reason_check
  CHECK (stop_reason IS NULL OR stop_reason IN ('review_passed','review_count_exhausted','retry_exhausted','total_timeout','review_verdict_hold','restart_orphaned'));
COMMIT;
