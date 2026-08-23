-- 086_ai_invoke_paused_review.sql
-- Preserve a paused/handed-off chain's [review] selections (per-step review count and
-- reviewer) across pause->resume and hop handoff (0414 P0007/L0008). Additive only: both
-- columns are NULL-able JSON-text payload the review gate re-derives from on every read
-- (L0008 2.3) -- pre-existing paused rows keep today's "no review gate" behaviour.
-- Same JSON-map storage convention 076a introduced on this table for its own two
-- per-step selection columns -- no new (de)serialization helper is needed.

BEGIN;

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_review_count_overrides TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_reviewer_overrides TEXT;

COMMIT;
