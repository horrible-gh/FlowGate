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
--   group_id    NOT NULL is relaxed: a branch_merge row has no group. The fk_gms_group foreign
--               key keeps its meaning (a NULL child references nothing); the column type is
--               unchanged, which is what InnoDB requires of a column inside a foreign key.
--
-- `kind` gains the value 'branch_merge' in code only (the column has no CHECK).
--
-- MySQL-only, the same premise 086/087/088 write down: no `IF NOT EXISTS` on ADD COLUMN /
-- CREATE INDEX (not available before 8.0.29 / at all). Re-application safety rests on the
-- migrations ledger keying on the filename, which applies this file exactly once — so this
-- file must never be renamed without a `migration_renames.RENAMES` entry.
--
-- Numbering: 122 is taken twice across live branches (test_spec_results / source_bundles);
-- 123 was free in all three dialects on every local and remote branch when this was written.

ALTER TABLE git_merge_session MODIFY COLUMN group_id VARCHAR(191) NULL;
ALTER TABLE git_merge_session ADD COLUMN owner_type VARCHAR(32) NULL;
ALTER TABLE git_merge_session ADD COLUMN project_id VARCHAR(191) NULL;

UPDATE git_merge_session SET owner_type = 'group' WHERE owner_type IS NULL;
UPDATE git_merge_session s
    JOIN group_git_state g ON g.group_id = s.group_id
    SET s.project_id = g.project_id
    WHERE s.project_id IS NULL;

CREATE INDEX idx_git_merge_session_project_status
    ON git_merge_session(project_id, status);
