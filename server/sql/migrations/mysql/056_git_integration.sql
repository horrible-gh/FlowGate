-- 056_git_integration.sql
-- Git integration (flowgate.default.0115 DB0007): per-project repository config,
-- per-group worktree ledger + finalize state machine, merge-conflict sessions,
-- project-level git mutex, and the grant group_id for worktree source resolution.
-- Additive only: 5 new tables + 1 new nullable column.
-- MySQL has no partial unique index: the "one open merge session per group"
-- invariant (DB0007 I3) is enforced by the service layer inside a transaction.

CREATE TABLE IF NOT EXISTS project_git_config (
    project_id              VARCHAR(191) NOT NULL PRIMARY KEY,
    repo_url                TEXT    NOT NULL,
    provider                VARCHAR(20) NOT NULL DEFAULT 'generic'
        CHECK (provider IN ('github','gitlab','gitea','gitbucket','generic')),
    username                TEXT    NULL,
    secret_enc              TEXT    NULL,
    base_branch             TEXT    NOT NULL DEFAULT 'main',
    default_finalize_action VARCHAR(10) NOT NULL DEFAULT 'wait'
        CHECK (default_finalize_action IN ('merge','push','wait')),
    enabled                 INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    CONSTRAINT fk_pgc_project
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_git_state (
    group_id            VARCHAR(191) NOT NULL PRIMARY KEY,
    project_id          VARCHAR(191) NOT NULL,
    branch              TEXT    NOT NULL,
    worktree_registered INTEGER NOT NULL DEFAULT 0 CHECK (worktree_registered IN (0,1)),
    status              VARCHAR(20) NOT NULL DEFAULT 'none'
        CHECK (status IN ('none','awaiting_choice','merging','conflict','merged','pushed','waiting')),
    merge_id            INT     NULL,
    merge_commit        TEXT    NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    CONSTRAINT fk_ggs_project
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS git_merge_session (
    merge_id   INT AUTO_INCREMENT PRIMARY KEY,
    group_id   VARCHAR(191) NOT NULL,
    status     VARCHAR(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','aborted')),
    created_at TEXT NOT NULL,
    closed_at  TEXT NULL,
    CONSTRAINT fk_gms_group
        FOREIGN KEY (group_id) REFERENCES group_git_state(group_id) ON DELETE CASCADE
);

ALTER TABLE group_git_state
    ADD CONSTRAINT fk_ggs_merge
    FOREIGN KEY (merge_id) REFERENCES git_merge_session(merge_id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS git_merge_session_file (
    merge_id    INT NOT NULL,
    path        VARCHAR(191) NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
    resolved_at TEXT NULL,
    PRIMARY KEY (merge_id, path),
    CONSTRAINT fk_gmsf_session
        FOREIGN KEY (merge_id) REFERENCES git_merge_session(merge_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS git_project_lock (
    project_id  VARCHAR(191) NOT NULL PRIMARY KEY,
    holder      TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    CONSTRAINT fk_gpl_project
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

ALTER TABLE remote_tool_grant ADD COLUMN group_id TEXT NULL;

CREATE INDEX idx_group_git_state_project
    ON group_git_state(project_id, status);
