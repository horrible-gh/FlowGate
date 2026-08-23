-- 088_tr_conflict_session.sql
-- flowgate.default.0332 TR0019 (R0001 / D0005 K5·K8 / P0006 §5-4 / L0007 §2.3):
-- a rewind or a forward restore whose revert hits a conflict used to be a dead end — the loop
-- destroyed the conflicted index (`revert --quit` + `reset --hard HEAD`) before anyone could look
-- at it. A TR conflict now becomes a session row in the SAME table the finalize merge uses, so
-- the panel's conflict editor, `list_conflicts`/`resolve_conflicts` and the `resolve_conflict` AI
-- run all reach it unchanged. Two nullable columns tell the two kinds apart:
--
--   kind     'merge' | 'tr_revert' | 'tr_reapply'. NULL reads as 'merge', so rows written by an
--            older build stay correct.
--   context  JSON: which worktree the conflict lives in, the ledger row it belongs to, the commit
--            subject/body the resolution must be committed with, the group status to return to on
--            abort, and the review state.
--
-- Deliberately NO new `status` value: 'resolved, waiting for a person to press commit' lives in
-- `context.review_state`. The sqlite dialect cannot widen a CHECK without rewriting a table that
-- has children, and the three dialects stay identical in shape.
--
-- `IF NOT EXISTS` follows the postgres-only deviation 086 records: postgres has supported it on
-- ADD COLUMN since 9.6, so the file is strictly safer with it.
--
-- Numbering: 087 is the highest ordinal this branch holds after its own renumber and 088 was free
-- in all three dialects and on origin/main when this file was written.
--
-- Additive only. Rollback is `ALTER TABLE git_merge_session DROP COLUMN kind` and the same for
-- `context`.

BEGIN;

ALTER TABLE git_merge_session ADD COLUMN IF NOT EXISTS kind TEXT;
ALTER TABLE git_merge_session ADD COLUMN IF NOT EXISTS context TEXT;

-- Every row that exists before this file is a finalize merge by definition.
UPDATE git_merge_session SET kind = 'merge' WHERE kind IS NULL;

COMMIT;
