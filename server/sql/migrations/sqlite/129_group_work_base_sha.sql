-- 129_group_work_base_sha.sql
-- flowgate.default.0665 T0004 (NR0003 §6/§7.2): a group's change-scope floor is a
-- RECORDED commit, never the current tip of a branch name.
--   work_base_sha          — the exact commit the group worktree forked from
--                            (rev-parse of the start point at creation / terminal reopen).
--   work_base_sync_sha     — the exact source commit the last successful
--                            update-from-base merged (NULL = never updated).
--   work_base_state        — verified | unverified | confirmed. NULL = not migrated
--                            yet, read exactly like unverified (scope is refused).
--   work_base_evidence     — JSON: origin of the values, unverified reason and
--                            candidates, confirming user and basis.
--   work_base_recorded_at  — when the state last changed.
-- group_work_base_sync_log is the audit trail: one row per fork, reopen fork,
-- update (clean or conflict-resolved), backfill and manual confirmation. It is the
-- trustworthy evidence a later audit needs to tell a FlowGate update from a manual
-- merge with the same subject (NR0003 §7.2 8).
-- Additive only. Existing groups are classified by the post-start backfill
-- (dry-run first), not by SQL: the classification needs Git.

BEGIN;

ALTER TABLE group_git_state ADD COLUMN work_base_sha         TEXT;
ALTER TABLE group_git_state ADD COLUMN work_base_sync_sha    TEXT;
ALTER TABLE group_git_state ADD COLUMN work_base_state       TEXT;
ALTER TABLE group_git_state ADD COLUMN work_base_evidence    TEXT;
ALTER TABLE group_git_state ADD COLUMN work_base_recorded_at TEXT;

CREATE TABLE group_work_base_sync_log (
    log_id       TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('fork', 'reopen_fork', 'update', 'backfill', 'manual_confirm')),
    source_ref   TEXT NULL,
    source_sha   TEXT NULL,
    result_head  TEXT NULL,
    merge_id     INTEGER NULL,
    actor        TEXT NULL,
    evidence     TEXT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_gwbsl_group ON group_work_base_sync_log(group_id, created_at);

COMMIT;
