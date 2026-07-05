-- 056_git_integration.sql
-- Git integration (flowgate.default.0115 DB0007): per-project repository config,
-- per-group worktree ledger + finalize state machine, merge-conflict sessions,
-- project-level git mutex, and the grant group_id for worktree source resolution.
-- Additive only: 5 new tables + 1 new nullable column.

BEGIN;

CREATE TABLE IF NOT EXISTS project_git_config (
    project_id              TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    repo_url                TEXT    NOT NULL,
    provider                TEXT    NOT NULL DEFAULT 'generic'
        CHECK (provider IN ('github','gitlab','gitea','gitbucket','generic')),
    username                TEXT,
    secret_enc              TEXT,
    base_branch             TEXT    NOT NULL DEFAULT 'main',
    default_finalize_action TEXT    NOT NULL DEFAULT 'wait'
        CHECK (default_finalize_action IN ('merge','push','wait')),
    enabled                 INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS group_git_state (
    group_id            TEXT PRIMARY KEY,
    project_id          TEXT    NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    branch              TEXT    NOT NULL,
    worktree_registered INTEGER NOT NULL DEFAULT 0 CHECK (worktree_registered IN (0,1)),
    status              TEXT    NOT NULL DEFAULT 'none'
        CHECK (status IN ('none','awaiting_choice','merging','conflict','merged','pushed','waiting')),
    merge_id            INTEGER REFERENCES git_merge_session(merge_id) ON DELETE SET NULL,
    merge_commit        TEXT,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS git_merge_session (
    merge_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   TEXT NOT NULL REFERENCES group_git_state(group_id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','aborted')),
    created_at TEXT NOT NULL,
    closed_at  TEXT
);

CREATE TABLE IF NOT EXISTS git_merge_session_file (
    merge_id    INTEGER NOT NULL REFERENCES git_merge_session(merge_id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
    resolved_at TEXT,
    PRIMARY KEY (merge_id, path)
);

CREATE TABLE IF NOT EXISTS git_project_lock (
    project_id  TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    holder      TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);

ALTER TABLE remote_tool_grant ADD COLUMN group_id TEXT;

CREATE INDEX IF NOT EXISTS idx_group_git_state_project
    ON group_git_state(project_id, status);

-- At most one open merge session per group (DB0007 I3).
CREATE UNIQUE INDEX IF NOT EXISTS uq_git_merge_session_open
    ON git_merge_session(group_id) WHERE status = 'open';

COMMIT;
