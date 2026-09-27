-- 123_git_merge_session_owner.sql
-- flowgate.default.0630 T0005 (D0004 §2.2/§3): an ordinary Branch Manager merge that
-- conflicts is now kept alive as a persistent attempt in git_merge_session instead of being
-- aborted on the spot. That merge belongs to a PROJECT, not to a group, so the session row
-- needs an owner that is not a group — and a fake group id is explicitly not an option.
--
--   owner_type  NULL | 'group' | 'branch_merge'. NULL reads as 'group' (every row written
--               before this file), so nothing has to backfill to stay correct.
--   project_id  the owning project. Always written for branch_merge rows; group rows keep
--               deriving it from group_id, and are backfilled here where the ledger row exists.
--   group_id    NOT NULL is relaxed: a branch_merge row has no group. The group FK and the
--               one-open-session-per-group unique index keep their meaning — NULL never collides.
--
-- `kind` gains the value 'branch_merge' in code only (the column has no CHECK).
-- Postgres drops a NOT NULL in place; `IF NOT EXISTS` follows the 086/088 postgres-only note.
--
-- Numbering: 122 is taken twice across live branches (test_spec_results / source_bundles);
-- 123 was free in all three dialects on every local and remote branch when this was written.

BEGIN;

ALTER TABLE git_merge_session ALTER COLUMN group_id DROP NOT NULL;
ALTER TABLE git_merge_session ADD COLUMN IF NOT EXISTS owner_type TEXT;
ALTER TABLE git_merge_session ADD COLUMN IF NOT EXISTS project_id TEXT;

UPDATE git_merge_session SET owner_type = 'group' WHERE owner_type IS NULL;
UPDATE git_merge_session SET project_id = (
    SELECT g.project_id FROM group_git_state g WHERE g.group_id = git_merge_session.group_id
) WHERE project_id IS NULL AND group_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_git_merge_session_project_status
    ON git_merge_session(project_id, status);

COMMIT;
