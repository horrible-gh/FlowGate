-- 141_tr_self_check_draft_owner.sql
-- flowgate.default.0638 T#1 (0003-NR §4 option A): a TR(new) worker may run Self-check before
-- its TR document exists. Such a run is owned by the issuing TR(new) token instead of a TR row,
-- and is linked to the TR when that token registers it.
--
--   tr_doc_id       NOT NULL is relaxed. NULL means "draft": the TR is not registered yet. The
--                   documents FK and its ON DELETE CASCADE keep their meaning for linked rows.
--   owner_token_id  the TR(new) token that started the draft run (tokens.token_id). No FK: a
--                   token row may be purged later and must not take linked TR evidence with it.
--   draft_doc_ref   that token's doc_ref (the workflow sequence owner) at start time, for audit.
--   linked_at       when the draft run was attached to the registered TR.
--
-- A row is either bound to a TR or fully owned by a draft token (CHECK). Existing rows all
-- carry tr_doc_id, so nothing is backfilled.
--
-- SQLite cannot drop a NOT NULL in place, so the table is recreated (the 125 procedure). No
-- other table references tr_self_check_runs, so renaming the old table rewrites no child.
PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE tr_self_check_runs RENAME TO tr_self_check_runs_before_141;

CREATE TABLE tr_self_check_runs (
    self_check_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    tr_doc_id TEXT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    owner_token_id TEXT NULL,
    draft_doc_ref TEXT NULL,
    linked_at TEXT NULL,
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
    active_key TEXT NULL UNIQUE CHECK (active_key IS NULL OR (length(active_key) = 64 AND active_key NOT GLOB '*[^0-9a-f]*')),
    exit_code INTEGER NULL,
    timed_out INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    stdout_tail TEXT NULL,
    stderr_tail TEXT NULL,
    source_head_before TEXT NULL,
    source_head_after TEXT NULL,
    source_branch_before TEXT NULL,
    source_branch_after TEXT NULL,
    source_refs_hash_before TEXT NULL CHECK (source_refs_hash_before IS NULL OR (length(source_refs_hash_before) = 64 AND source_refs_hash_before NOT GLOB '*[^0-9a-f]*')),
    source_refs_hash_after TEXT NULL CHECK (source_refs_hash_after IS NULL OR (length(source_refs_hash_after) = 64 AND source_refs_hash_after NOT GLOB '*[^0-9a-f]*')),
    source_index_hash_before TEXT NULL CHECK (source_index_hash_before IS NULL OR (length(source_index_hash_before) = 64 AND source_index_hash_before NOT GLOB '*[^0-9a-f]*')),
    source_index_hash_after TEXT NULL CHECK (source_index_hash_after IS NULL OR (length(source_index_hash_after) = 64 AND source_index_hash_after NOT GLOB '*[^0-9a-f]*')),
    source_status_hash_before TEXT NULL CHECK (source_status_hash_before IS NULL OR (length(source_status_hash_before) = 64 AND source_status_hash_before NOT GLOB '*[^0-9a-f]*')),
    source_status_hash_after TEXT NULL CHECK (source_status_hash_after IS NULL OR (length(source_status_hash_after) = 64 AND source_status_hash_after NOT GLOB '*[^0-9a-f]*')),
    index_lock_before INTEGER NULL CHECK (index_lock_before IS NULL OR index_lock_before IN (0, 1)),
    index_lock_after INTEGER NULL CHECK (index_lock_after IS NULL OR index_lock_after IN (0, 1)),
    source_changed_during_run INTEGER NOT NULL DEFAULT 0 CHECK (source_changed_during_run IN (0, 1)),
    worktree_state_changed INTEGER NOT NULL DEFAULT 0 CHECK (worktree_state_changed IN (0, 1)),
    source_lock_holder TEXT NULL,
    process_owner_kind TEXT NULL,
    target_pid INTEGER NULL,
    target_start_identity TEXT NULL,
    supervisor_pid INTEGER NULL,
    supervisor_start_identity TEXT NULL,
    recovery_state TEXT NOT NULL DEFAULT 'none' CHECK (recovery_state IN ('none', 'recovering', 'incomplete', 'recovered')),
    recovery_reason TEXT NULL,
    error_code TEXT NULL,
    cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0, 1)),
    cleanup_error TEXT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT NULL,
    finished_at TEXT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT ck_selfcheck_owner CHECK (tr_doc_id IS NOT NULL OR (owner_token_id IS NOT NULL AND draft_doc_ref IS NOT NULL))
);

INSERT INTO tr_self_check_runs (
    self_check_run_id, project_id, group_id, tr_doc_id, requested_by, policy_version, program,
    args_json, resolved_executable_path, resolved_executable_name, executable_origin, cwd_relative,
    timeout_seconds, env_keys_json, status, active_key, exit_code, timed_out, cancel_requested,
    stdout_tail, stderr_tail, source_head_before, source_head_after, source_branch_before,
    source_branch_after, source_refs_hash_before, source_refs_hash_after, source_index_hash_before,
    source_index_hash_after, source_status_hash_before, source_status_hash_after, index_lock_before,
    index_lock_after, source_changed_during_run, worktree_state_changed, source_lock_holder,
    process_owner_kind, target_pid, target_start_identity, supervisor_pid, supervisor_start_identity,
    recovery_state, recovery_reason, error_code, cleanup_pending, cleanup_error, created_at,
    started_at, finished_at, updated_at
)
SELECT
    self_check_run_id, project_id, group_id, tr_doc_id, requested_by, policy_version, program,
    args_json, resolved_executable_path, resolved_executable_name, executable_origin, cwd_relative,
    timeout_seconds, env_keys_json, status, active_key, exit_code, timed_out, cancel_requested,
    stdout_tail, stderr_tail, source_head_before, source_head_after, source_branch_before,
    source_branch_after, source_refs_hash_before, source_refs_hash_after, source_index_hash_before,
    source_index_hash_after, source_status_hash_before, source_status_hash_after, index_lock_before,
    index_lock_after, source_changed_during_run, worktree_state_changed, source_lock_holder,
    process_owner_kind, target_pid, target_start_identity, supervisor_pid, supervisor_start_identity,
    recovery_state, recovery_reason, error_code, cleanup_pending, cleanup_error, created_at,
    started_at, finished_at, updated_at
FROM tr_self_check_runs_before_141;

DROP TABLE tr_self_check_runs_before_141;

CREATE INDEX idx_selfcheck_doc_created ON tr_self_check_runs (tr_doc_id, created_at DESC, self_check_run_id DESC);
CREATE INDEX idx_selfcheck_project_group_status ON tr_self_check_runs (project_id, group_id, status);
CREATE INDEX idx_selfcheck_recovery ON tr_self_check_runs (recovery_state, project_id);
CREATE INDEX idx_selfcheck_finished ON tr_self_check_runs (finished_at);
CREATE INDEX idx_selfcheck_owner_created ON tr_self_check_runs (owner_token_id, created_at DESC, self_check_run_id DESC);

COMMIT;
PRAGMA foreign_keys=ON;
