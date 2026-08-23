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
-- `context.review_state`, and the three dialects stay identical in shape.
--
-- MySQL-only, the same premise 086 and 087 write down: `ADD COLUMN` carries no `IF NOT EXISTS`
-- (that syntax is not available before 8.0.29). Re-application safety rests on the migrations
-- ledger keying on the filename, which applies this file exactly once — so this file must never
-- be renamed without a `migration_renames.RENAMES` entry ([[renamed-migration-reruns-on-old-dbs]]).
--
-- Numbering: 087 is the highest ordinal this branch holds after its own renumber and 088 was free
-- in all three dialects and on origin/main when this file was written.
--
-- Additive only. Rollback is `ALTER TABLE git_merge_session DROP COLUMN kind` and the same for
-- `context`.

ALTER TABLE git_merge_session ADD COLUMN kind TEXT NULL;
ALTER TABLE git_merge_session ADD COLUMN context TEXT NULL;

-- Every row that exists before this file is a finalize merge by definition.
UPDATE git_merge_session SET kind = 'merge' WHERE kind IS NULL;
