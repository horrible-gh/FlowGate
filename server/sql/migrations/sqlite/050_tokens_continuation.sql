-- 050_tokens_continuation.sql
-- Continuous work (unmanned continuous work) — group 0051 R0001, NR0003 B안 foundation.
-- Basis: NR0003 §4 (B안), §6-② "action_scope 신규 값 대신 new + 토큰 메타 플래그" (recommended).
--
-- A continuous-mode token carries WHERE the unmanned chain should stop (the target
-- workflow_sequence_items.item_seq) and WHETHER it runs in AI review mode (Q&A-first,
-- per T0004/CH0006). Both are NULL/0 for every ordinary token, so the existing
-- new/edit/review/workflow_decide flow is untouched (additive columns only — same
-- pattern as 043 dry_run_count / 016 scratch_dir, no table rebuild, no CHECK change).
--
-- action_scope stays 'new' (NR0003 §6-② chose the metadata route over a new scope to
-- avoid migrating the action_scope CHECK constraint and every scope-gated code path).
--   continuation_target_seq  : target item_seq the chain advances toward; NULL = not a
--                              continuation token (ordinary single-step token).
--   continuation_review_mode : 1 = AI review mode (pause for human Q&A before chaining,
--                              T0004/CH0006); 0 = straight continuous run.

ALTER TABLE tokens ADD COLUMN continuation_target_seq INTEGER;
ALTER TABLE tokens ADD COLUMN continuation_review_mode INTEGER NOT NULL DEFAULT 0;
