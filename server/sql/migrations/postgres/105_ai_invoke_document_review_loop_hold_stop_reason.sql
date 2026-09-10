-- 105_ai_invoke_document_review_loop_hold_stop_reason.sql
-- flowgate.default.0486 T0011 section 3 / NR0010 Finding 2: standalone document review
-- loops must record 'hold' as a durable human stop (stop_reason='review_verdict_hold'),
-- the same code the continuous review gate already uses
-- (REVIEW_VERDICT_HOLD_STOP_CODE). The 091 CHECK constraint on stop_reason only allowed
-- the four terminal reasons that existed before this loop's hold handling was fixed, so
-- writing a hold stop failed the CHECK.
--
-- The constraint name follows Postgres's own default naming for an unnamed column CHECK
-- (<table>_<column>_check) -- the same convention 092's DROP CONSTRAINT
-- ai_invoke_document_review_loops_run_id_fkey already relied on for this table.

BEGIN;

ALTER TABLE ai_invoke_document_review_loops
  DROP CONSTRAINT ai_invoke_document_review_loops_stop_reason_check;

ALTER TABLE ai_invoke_document_review_loops
  ADD CONSTRAINT ai_invoke_document_review_loops_stop_reason_check
  CHECK (stop_reason IS NULL OR stop_reason IN ('review_passed','review_count_exhausted','retry_exhausted','total_timeout','review_verdict_hold'));

COMMIT;
