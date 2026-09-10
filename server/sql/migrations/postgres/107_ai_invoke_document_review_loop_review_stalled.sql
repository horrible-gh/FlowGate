-- 107_ai_invoke_document_review_loop_review_stalled.sql
-- flowgate.default.0486 T0029 item 2 (NR0028 F2): widen the durable loop terminal reason by
-- one value so the stall detector's stop_reason='review_stalled' can actually be stored.
-- Without it the checkpoint UPDATE raised IntegrityError and rolled back the automatic
-- document rejection written in the same transaction.
BEGIN;
ALTER TABLE ai_invoke_document_review_loops
  DROP CONSTRAINT ai_invoke_document_review_loops_stop_reason_check;
ALTER TABLE ai_invoke_document_review_loops
  ADD CONSTRAINT ai_invoke_document_review_loops_stop_reason_check
  CHECK (stop_reason IS NULL OR stop_reason IN ('review_passed','review_count_exhausted','retry_exhausted','total_timeout','review_verdict_hold','restart_orphaned','review_stalled'));
COMMIT;
