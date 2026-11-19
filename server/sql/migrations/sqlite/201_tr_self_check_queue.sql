-- 201_tr_self_check_queue.sql
-- flowgate.v02test.0053 1-B (T0005): the Self-check queue.
--
-- run_self_check no longer runs at once under the Group lock (G). It records the request as
-- status 'queued' with a copy (snapshot) of the worktree taken at that moment, and returns.
-- Queue workers inside the server start queued runs oldest first, alternating between
-- projects, inside the existing host processor cap; the run executes in its snapshot and
-- never takes G, so a Group may have any number of runs queued or running.
--
-- tr_self_check_runs:
--   status               gains 'queued' (waiting for a queue worker; holds no reservation).
--   enqueued_at          the wait clock: when the request was queued (again, after a system
--                        re-queue). NULL =
--                        a run admitted before this migration (worktree run under G).
--   snapshot_path        the run's snapshot folder (the tree it executes in, plus meta.json).
--   queue_key            the card the request came from (Graph attempt, TR or draft owner):
--                        a newer request of the same card cancels its older queued ones.
--   queue_lease_owner    the server instance whose queue worker claimed the run.
--   queue_lease_until    the claim's lease; renewed while the worker drives the run. A claim
--                        whose lease lapsed before any process was spawned goes back to the
--                        queue for another worker.
--   queue_requeue_count  system re-queues after the wait limit (at most one).
--   queue_order          the request's place in the queue (UTC, microseconds), set once: a
--                        system re-queue keeps it, so the oldest request still goes first.
--   queue_claimed_at     when a queue worker last claimed the run (UTC, microseconds); the
--                        project whose last claim is the oldest goes next.
-- Existing rows: all new columns NULL / 0; nothing is backfilled.
--
-- SQLite cannot change a CHECK in place: the table is rebuilt with the 195 procedure (new table,
-- copy, drop, rename). tr_self_check_run_progress references tr_self_check_runs by name, so the
-- renamed new table takes over that reference.
PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS tr_self_check_runs_new;

CREATE TABLE tr_self_check_runs_new (
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
    status TEXT NOT NULL CHECK (status IN ('queued', 'pending', 'running', 'completed', 'failed', 'cancelled')),
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
    owner_instance_id TEXT NULL,
    owner_marker TEXT NULL,
    attempt_id TEXT NULL,
    input_commit TEXT NULL,
    result_summary TEXT NULL,
    environment_evidence TEXT NULL,
    host_node_key TEXT NULL,
    cpu_reservation INTEGER NULL,
    cpu_share_requested INTEGER NULL,
    enqueued_at TEXT NULL,
    snapshot_path TEXT NULL,
    queue_key TEXT NULL,
    queue_lease_owner TEXT NULL,
    queue_lease_until TEXT NULL,
    queue_requeue_count INTEGER NOT NULL DEFAULT 0 CHECK (queue_requeue_count >= 0),
    queue_order TEXT NULL,
    queue_claimed_at TEXT NULL,
    CONSTRAINT ck_selfcheck_owner CHECK (tr_doc_id IS NOT NULL OR (owner_token_id IS NOT NULL AND draft_doc_ref IS NOT NULL))
);

INSERT INTO tr_self_check_runs_new (
    self_check_run_id, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, linked_at,
    requested_by, policy_version, program, args_json, resolved_executable_path,
    resolved_executable_name, executable_origin, cwd_relative, timeout_seconds, env_keys_json,
    status, active_key, exit_code, timed_out, cancel_requested, stdout_tail, stderr_tail,
    source_head_before, source_head_after, source_branch_before, source_branch_after,
    source_refs_hash_before, source_refs_hash_after, source_index_hash_before,
    source_index_hash_after, source_status_hash_before, source_status_hash_after,
    index_lock_before, index_lock_after, source_changed_during_run, worktree_state_changed,
    source_lock_holder, process_owner_kind, target_pid, target_start_identity, supervisor_pid,
    supervisor_start_identity, recovery_state, recovery_reason, error_code, cleanup_pending,
    cleanup_error, created_at, started_at, finished_at, updated_at, owner_instance_id,
    owner_marker, attempt_id, input_commit, result_summary, environment_evidence, host_node_key,
    cpu_reservation, cpu_share_requested
)
SELECT
    self_check_run_id, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, linked_at,
    requested_by, policy_version, program, args_json, resolved_executable_path,
    resolved_executable_name, executable_origin, cwd_relative, timeout_seconds, env_keys_json,
    status, active_key, exit_code, timed_out, cancel_requested, stdout_tail, stderr_tail,
    source_head_before, source_head_after, source_branch_before, source_branch_after,
    source_refs_hash_before, source_refs_hash_after, source_index_hash_before,
    source_index_hash_after, source_status_hash_before, source_status_hash_after,
    index_lock_before, index_lock_after, source_changed_during_run, worktree_state_changed,
    source_lock_holder, process_owner_kind, target_pid, target_start_identity, supervisor_pid,
    supervisor_start_identity, recovery_state, recovery_reason, error_code, cleanup_pending,
    cleanup_error, created_at, started_at, finished_at, updated_at, owner_instance_id,
    owner_marker, attempt_id, input_commit, result_summary, environment_evidence, host_node_key,
    cpu_reservation, cpu_share_requested
FROM tr_self_check_runs;

DROP TABLE tr_self_check_runs;
ALTER TABLE tr_self_check_runs_new RENAME TO tr_self_check_runs;

CREATE INDEX idx_selfcheck_doc_created ON tr_self_check_runs (tr_doc_id, created_at DESC, self_check_run_id DESC);
CREATE INDEX idx_selfcheck_project_group_status ON tr_self_check_runs (project_id, group_id, status);
CREATE INDEX idx_selfcheck_recovery ON tr_self_check_runs (recovery_state, project_id);
CREATE INDEX idx_selfcheck_finished ON tr_self_check_runs (finished_at);
CREATE INDEX idx_selfcheck_owner_created ON tr_self_check_runs (owner_token_id, created_at DESC, self_check_run_id DESC);
CREATE INDEX IF NOT EXISTS idx_tr_self_check_runs_group_id ON tr_self_check_runs (group_id);
CREATE INDEX idx_selfcheck_queue ON tr_self_check_runs (status, queue_order);
CREATE INDEX idx_selfcheck_queue_key ON tr_self_check_runs (queue_key, status);

COMMIT;
PRAGMA foreign_keys=ON;
