-- 088_tr_conflict_session.sql
-- flowgate.default.0332 TR0019 (R0001 / D0005 K5·K8 / P0006 §5-4 / L0007 §2.3):
-- a rewind or a forward restore whose revert hits a conflict used to be a dead end. The loop
-- called `restore_after_failed_revert` — `revert --quit` + `reset --hard HEAD` — which destroyed
-- the conflicted index before anyone, human or AI, could look at it. The screen could only say
-- "a conflict; sort it out by hand", and the one machine FlowGate already owns for exactly this
-- job (the finalize merge's conflict session, its inline editor and its `resolve_conflict` AI
-- run) could not be pointed at it because that machine is keyed on a merge session row.
--
-- So a TR conflict becomes a session row in the SAME table. Nothing new is invented: the group
-- goes to `status='conflict'` with a `merge_id` exactly as a merge conflict does, the panel's
-- existing conflict editor opens it, `list_conflicts`/`resolve_conflicts` serve it, and the AI
-- conflict token is issued against the same `merge_id`. Two nullable columns are all that is
-- needed to tell the two apart:
--
--   kind     'merge' (the finalize merge — the only kind that existed before this file),
--            'tr_revert' (a rewind cancelling a TR commit) or 'tr_reapply' (a forward restore
--            putting one back). NULL is read as 'merge' by the code, so a row written by an
--            older build stays correct.
--   context  JSON: which worktree the conflict lives in, the ledger row it belongs to, the
--            commit subject/body the resolution must be committed with, the group status to
--            return to on abort, and the review state. A merge session's conflict is in the
--            base checkout and finishes as a merge commit; a TR session's is in the group
--            worktree and finishes as a revert commit — everything that differs is here.
--
-- Deliberately NO new `status` value. `git_merge_session.status` is CHECK-constrained to
-- ('open','done','aborted') and widening a CHECK is a full table rewrite on SQLite, on a table
-- with two children (git_merge_session_file, group_git_state.merge_id) — the 042/052 accident
-- (see [[sqlite-rename-rewrites-child-references]]). The "resolved, waiting for a person to
-- press commit" step lives in `context.review_state` instead, which is the honest place for it:
-- it is a property of this session's workflow, not of the session's lifecycle.
--
-- Numbering: 087 is the highest ordinal this branch holds after its own renumber and 088 was
-- free in all three dialects and on origin/main when this file was written.
--
-- Additive only. Rollback is `ALTER TABLE git_merge_session DROP COLUMN kind` and the same for
-- `context`.

BEGIN;

ALTER TABLE git_merge_session ADD COLUMN kind TEXT;
ALTER TABLE git_merge_session ADD COLUMN context TEXT;

-- Every row that exists before this file is a finalize merge by definition.
UPDATE git_merge_session SET kind = 'merge' WHERE kind IS NULL;

COMMIT;
