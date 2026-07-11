-- 061_git_untangle.sql
-- Git tangle prevention (flowgate.default.0205: B0001 -> D0002/P0003/L0004/DB0005).
-- Additive-only: 3 nullable columns + 1 backfill. No new tables, no new status
-- values, no CHECK (aligned with the SQLite authoring source). git_project_lock
-- is unchanged (its role narrows to short operation locks — a usage change).
--   git_merge_session.touched_at         — last activity time (ISO8601); sweep TTL basis (L0004 §1).
--   group_git_state.provision_error      — last worktree provisioning failure reason (L0004 §2.4).
--   group_git_state.provision_failed_at  — time of that failure; paired with provision_error.

ALTER TABLE git_merge_session ADD COLUMN touched_at TEXT NULL;
UPDATE git_merge_session SET touched_at = created_at WHERE touched_at IS NULL;
ALTER TABLE group_git_state ADD COLUMN provision_error TEXT NULL;
ALTER TABLE group_git_state ADD COLUMN provision_failed_at TEXT NULL;
