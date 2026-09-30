-- 127_tr_self_check_runs.sql
-- flowgate.default.0650 T0002 / 0634 0007-DB: TR Self-check database schema and project settings

ALTER TABLE project_settings
ADD COLUMN tr_self_check_enabled INTEGER NOT NULL DEFAULT 0
CHECK (tr_self_check_enabled IN (0, 1));

CREATE TABLE tr_self_check_runs (
    self_check_run_id VARCHAR(191) PRIMARY KEY,
    project_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    tr_doc_id VARCHAR(191) NOT NULL,
    requested_by VARCHAR(191) NULL,
    policy_version VARCHAR(64) NOT NULL,
    program VARCHAR(255) NOT NULL,
    args_json MEDIUMTEXT NOT NULL,
    resolved_executable_path MEDIUMTEXT NOT NULL,
    resolved_executable_name VARCHAR(255) NOT NULL,
    executable_origin VARCHAR(64) NOT NULL,
    cwd_relative MEDIUMTEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds > 0),
    env_keys_json MEDIUMTEXT NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    active_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL UNIQUE CHECK (active_key IS NULL OR (CHAR_LENGTH(active_key) = 64 AND active_key REGEXP '^[0-9a-f]{64}$')),
    exit_code INTEGER NULL,
    timed_out INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    stdout_tail MEDIUMTEXT NULL,
    stderr_tail MEDIUMTEXT NULL,
    source_head_before VARCHAR(64) NULL,
    source_head_after VARCHAR(64) NULL,
    source_branch_before MEDIUMTEXT NULL,
    source_branch_after MEDIUMTEXT NULL,
    source_refs_hash_before CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_refs_hash_before IS NULL OR (CHAR_LENGTH(source_refs_hash_before) = 64 AND source_refs_hash_before REGEXP '^[0-9a-f]{64}$')),
    source_refs_hash_after CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_refs_hash_after IS NULL OR (CHAR_LENGTH(source_refs_hash_after) = 64 AND source_refs_hash_after REGEXP '^[0-9a-f]{64}$')),
    source_index_hash_before CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_index_hash_before IS NULL OR (CHAR_LENGTH(source_index_hash_before) = 64 AND source_index_hash_before REGEXP '^[0-9a-f]{64}$')),
    source_index_hash_after CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_index_hash_after IS NULL OR (CHAR_LENGTH(source_index_hash_after) = 64 AND source_index_hash_after REGEXP '^[0-9a-f]{64}$')),
    source_status_hash_before CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_status_hash_before IS NULL OR (CHAR_LENGTH(source_status_hash_before) = 64 AND source_status_hash_before REGEXP '^[0-9a-f]{64}$')),
    source_status_hash_after CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (source_status_hash_after IS NULL OR (CHAR_LENGTH(source_status_hash_after) = 64 AND source_status_hash_after REGEXP '^[0-9a-f]{64}$')),
    index_lock_before INTEGER NULL CHECK (index_lock_before IS NULL OR index_lock_before IN (0, 1)),
    index_lock_after INTEGER NULL CHECK (index_lock_after IS NULL OR index_lock_after IN (0, 1)),
    source_changed_during_run INTEGER NOT NULL DEFAULT 0 CHECK (source_changed_during_run IN (0, 1)),
    worktree_state_changed INTEGER NOT NULL DEFAULT 0 CHECK (worktree_state_changed IN (0, 1)),
    source_lock_holder VARCHAR(255) NULL,
    process_owner_kind VARCHAR(64) NULL,
    target_pid BIGINT NULL,
    target_start_identity MEDIUMTEXT NULL,
    supervisor_pid BIGINT NULL,
    supervisor_start_identity MEDIUMTEXT NULL,
    recovery_state VARCHAR(32) NOT NULL DEFAULT 'none' CHECK (recovery_state IN ('none', 'recovering', 'incomplete', 'recovered')),
    recovery_reason MEDIUMTEXT NULL,
    error_code VARCHAR(64) NULL,
    cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0, 1)),
    cleanup_error MEDIUMTEXT NULL,
    created_at VARCHAR(40) NOT NULL,
    started_at VARCHAR(40) NULL,
    finished_at VARCHAR(40) NULL,
    updated_at VARCHAR(40) NOT NULL,
    CONSTRAINT fk_selfcheck_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_selfcheck_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_selfcheck_document FOREIGN KEY (tr_doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE,
    CONSTRAINT fk_selfcheck_user FOREIGN KEY (requested_by) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX idx_selfcheck_doc_created ON tr_self_check_runs (tr_doc_id, created_at DESC, self_check_run_id DESC);
CREATE INDEX idx_selfcheck_project_group_status ON tr_self_check_runs (project_id, group_id, status);
CREATE INDEX idx_selfcheck_recovery ON tr_self_check_runs (recovery_state, project_id);
CREATE INDEX idx_selfcheck_finished ON tr_self_check_runs (finished_at);
