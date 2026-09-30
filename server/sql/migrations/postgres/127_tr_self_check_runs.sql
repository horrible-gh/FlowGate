-- 127_tr_self_check_runs.sql
-- flowgate.default.0650 T0002 / 0634 0007-DB: TR Self-check database schema and project settings

ALTER TABLE project_settings
ADD COLUMN tr_self_check_enabled INTEGER NOT NULL DEFAULT 0
CHECK (tr_self_check_enabled IN (0, 1));

CREATE TABLE tr_self_check_runs (
    self_check_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    tr_doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    requested_by TEXT NULL REFERENCES users(user_id) ON DELETE SET NULL,
    policy_version TEXT NOT NULL,
    program TEXT NOT NULL,
    args_json TEXT NOT NULL,
    resolved_executable_path TEXT NOT NULL,
    resolved_executable_name TEXT NOT NULL,
    executable_origin TEXT NOT NULL,
    cwd_relative TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds > 0),
    env_keys_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    active_key VARCHAR(64) NULL UNIQUE CHECK (active_key IS NULL OR active_key ~ '^[0-9a-f]{64}$'),
    exit_code INTEGER NULL,
    timed_out INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    stdout_tail TEXT NULL,
    stderr_tail TEXT NULL,
    source_head_before TEXT NULL,
    source_head_after TEXT NULL,
    source_branch_before TEXT NULL,
    source_branch_after TEXT NULL,
    source_refs_hash_before VARCHAR(64) NULL CHECK (source_refs_hash_before IS NULL OR source_refs_hash_before ~ '^[0-9a-f]{64}$'),
    source_refs_hash_after VARCHAR(64) NULL CHECK (source_refs_hash_after IS NULL OR source_refs_hash_after ~ '^[0-9a-f]{64}$'),
    source_index_hash_before VARCHAR(64) NULL CHECK (source_index_hash_before IS NULL OR source_index_hash_before ~ '^[0-9a-f]{64}$'),
    source_index_hash_after VARCHAR(64) NULL CHECK (source_index_hash_after IS NULL OR source_index_hash_after ~ '^[0-9a-f]{64}$'),
    source_status_hash_before VARCHAR(64) NULL CHECK (source_status_hash_before IS NULL OR source_status_hash_before ~ '^[0-9a-f]{64}$'),
    source_status_hash_after VARCHAR(64) NULL CHECK (source_status_hash_after IS NULL OR source_status_hash_after ~ '^[0-9a-f]{64}$'),
    index_lock_before INTEGER NULL CHECK (index_lock_before IS NULL OR index_lock_before IN (0, 1)),
    index_lock_after INTEGER NULL CHECK (index_lock_after IS NULL OR index_lock_after IN (0, 1)),
    source_changed_during_run INTEGER NOT NULL DEFAULT 0 CHECK (source_changed_during_run IN (0, 1)),
    worktree_state_changed INTEGER NOT NULL DEFAULT 0 CHECK (worktree_state_changed IN (0, 1)),
    source_lock_holder TEXT NULL,
    process_owner_kind TEXT NULL,
    target_pid BIGINT NULL,
    target_start_identity TEXT NULL,
    supervisor_pid BIGINT NULL,
    supervisor_start_identity TEXT NULL,
    recovery_state TEXT NOT NULL DEFAULT 'none' CHECK (recovery_state IN ('none', 'recovering', 'incomplete', 'recovered')),
    recovery_reason TEXT NULL,
    error_code TEXT NULL,
    cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0, 1)),
    cleanup_error TEXT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT NULL,
    finished_at TEXT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_selfcheck_doc_created ON tr_self_check_runs (tr_doc_id, created_at DESC, self_check_run_id DESC);
CREATE INDEX idx_selfcheck_project_group_status ON tr_self_check_runs (project_id, group_id, status);
CREATE INDEX idx_selfcheck_recovery ON tr_self_check_runs (recovery_state, project_id);
CREATE INDEX idx_selfcheck_finished ON tr_self_check_runs (finished_at);
