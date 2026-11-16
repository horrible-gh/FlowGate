-- 192_ai_invoke_document_review_loop_review_count_range.sql (postgres)
-- flowgate.v02test.0027 0007-T (TR0008 rev1 rejection): the loop's review_count CHECK still froze (-1,1,2,3)
-- although the allowed count is the policy's (-1, 1..max, max 1..30): a card's result review loop asking for 5
-- under max 5 raised IntegrityError. Widen it to the graph_stage_assignments.review_rounds rule (164):
-- review_count = -1 OR review_count >= 1. The 091 inline column CHECK has the default name below.
-- DOWN: swap back to CHECK (review_count IN (-1,1,2,3)) after rows above 3 are gone (not automatic).
BEGIN;
ALTER TABLE ai_invoke_document_review_loops
  DROP CONSTRAINT IF EXISTS ai_invoke_document_review_loops_review_count_check;
ALTER TABLE ai_invoke_document_review_loops
  ADD CONSTRAINT ai_invoke_document_review_loops_review_count_check
  CHECK (review_count = -1 OR review_count >= 1);
COMMIT;
