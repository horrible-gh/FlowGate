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
--
-- SQLite cannot drop a NOT NULL in place, so the table is recreated (the 044/025 procedure):
-- a NEW table is created, filled with every row (merge_id preserved), the old one dropped and
-- the new one renamed into its place. The OLD table is never renamed, so the two children that
-- reference it by name (git_merge_session_file.merge_id, group_git_state.merge_id) are not
-- rewritten to point at a temporary name ([[sqlite-rename-rewrites-child-references]]).
-- foreign_keys is OFF around the swap so dropping the old table does not cascade into them.
--
-- Numbering: 122 is taken twice across live branches (test_spec_results / source_bundles);
-- 123 was free in all three dialects on every local and remote branch when this was written.

PRAGMA foreign_keys = OFF;
BEGIN;

CREATE TABLE git_merge_session__new (
    merge_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        TEXT REFERENCES group_git_state(group_id) ON DELETE CASCADE,  -- was NOT NULL
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','aborted')),
    created_at      TEXT NOT NULL,
    closed_at       TEXT,
    touched_at      TEXT,
    finalize_action TEXT,
    kind            TEXT,
    context         TEXT,
    owner_type      TEXT,
    project_id      TEXT
);

INSERT INTO git_merge_session__new
    (merge_id, group_id, status, created_at, closed_at, touched_at, finalize_action, kind, context,
     owner_type, project_id)
SELECT s.merge_id, s.group_id, s.status, s.created_at, s.closed_at, s.touched_at,
       s.finalize_action, s.kind, s.context,
       'group',
       (SELECT g.project_id FROM group_git_state g WHERE g.group_id = s.group_id)
FROM git_merge_session s;

DROP TABLE git_merge_session;
ALTER TABLE git_merge_session__new RENAME TO git_merge_session;

CREATE UNIQUE INDEX IF NOT EXISTS uq_git_merge_session_open
    ON git_merge_session(group_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_git_merge_session_project_status
    ON git_merge_session(project_id, status);

COMMIT;
PRAGMA foreign_keys = ON;
