-- 159_operation_job_gate_publish_kind.sql — flowgate.v02.0085 T#1 (D0007 §7.2 operation job)
-- Registers the job kind ``gate_publish`` (a confirm gate's publish: Git apply, archive, completion; its request
-- key is the gate decision id). The executor is a later card: a kind without an executor is never claimed.
--
-- SQLite cannot widen a CHECK in place, so operation_job is rebuilt: every column, every row and every index is
-- kept. Other tables point at operation_job (resource_lock.job_id, reservation.job_id,
-- group_git_state.approval_freeze_job_id), so the table is NOT renamed away first (SQLite would rewrite those
-- REFERENCES clauses to the throwaway name, see 074a / 114): the replacement is built under a new name, the data
-- copied, the original dropped and the replacement renamed into place. Nothing references operation_job_new.
-- Safe to run again: the replacement name is cleared first and a second run rebuilds the same table.
PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS operation_job_new;

CREATE TABLE operation_job_new (
    job_id              VARCHAR(30)  NOT NULL PRIMARY KEY,
    kind                VARCHAR(32)  NOT NULL CHECK (kind IN ('final_approval_publish', 'branch_merge_publish', 'worktree_provision', 'worktree_cleanup', 'base_publish', 'archive_preserve', 'archive_restore', 'archive_purge', 'project_provision', 'gate_publish')),
    project_id          TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    group_id            TEXT NULL,
    target_key          TEXT NULL,
    request_key         TEXT NOT NULL,
    request_key_hash    CHAR(64)     NOT NULL,
    request_fingerprint CHAR(64)     NOT NULL,
    has_freeze_phase    INTEGER      NOT NULL DEFAULT 0 CHECK (has_freeze_phase IN (0, 1)),
    status              VARCHAR(20)  NOT NULL CHECK (status IN ('freezing', 'freeze_wait', 'pending', 'running', 'blocked', 'retry_wait', 'recovery_required', 'succeeded', 'failed', 'cancelled')),
    phase               VARCHAR(40)  NULL,
    payload             TEXT NULL,
    evidence            TEXT NULL,
    freeze_record       TEXT NULL,
    freeze_completed    INTEGER      NOT NULL DEFAULT 0 CHECK (freeze_completed IN (0, 1)),
    frozen_sha          VARCHAR(64)  NULL,
    frozen_tree         VARCHAR(64)  NULL,
    pin_ref             VARCHAR(80)  NULL,
    attempt_count       INTEGER      NOT NULL DEFAULT 0,
    available_at        VARCHAR(25)  NOT NULL,
    lease_owner         VARCHAR(37)  NULL REFERENCES server_instance(instance_id) ON DELETE RESTRICT,
    lease_token         VARCHAR(35)  NULL,
    lease_until         VARCHAR(25)  NULL,
    claimed_from        VARCHAR(20)  NULL,
    blocked_domain      VARCHAR(1)   NULL CHECK (blocked_domain IS NULL OR blocked_domain IN ('P', 'G', 'W', 'B', 'R', 'M')),
    blocked_lock_key    VARCHAR(66)  NULL,
    blocked_holder      VARCHAR(120) NULL,
    blocked_operation   VARCHAR(32)  NULL,
    wait_started_at     VARCHAR(25)  NULL,
    last_error_code     VARCHAR(64)  NULL,
    retryable           INTEGER      NULL CHECK (retryable IS NULL OR retryable IN (0, 1)),
    cancel_requested    INTEGER      NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    release_intent      TEXT NULL,
    reconcile_claimed   INTEGER      NOT NULL DEFAULT 0 CHECK (reconcile_claimed IN (0, 1)),
    reconcile_reason    VARCHAR(20)  NULL CHECK (reconcile_reason IS NULL OR reconcile_reason IN ('lease_expired', 'owner_dead')),
    recovery_since      VARCHAR(25)  NULL,
    phase_note          VARCHAR(40)  NULL,
    requested_by        TEXT NULL,
    result              TEXT NULL,
    created_at          VARCHAR(25)  NOT NULL,
    updated_at          VARCHAR(25)  NOT NULL,
    finished_at         VARCHAR(25)  NULL,
    touch_seq           INTEGER      NOT NULL DEFAULT 0,
    CHECK (freeze_completed = 0 OR (frozen_sha IS NOT NULL AND frozen_tree IS NOT NULL AND pin_ref IS NOT NULL))
);

INSERT INTO operation_job_new (
    job_id,
    kind,
    project_id,
    group_id,
    target_key,
    request_key,
    request_key_hash,
    request_fingerprint,
    has_freeze_phase,
    status,
    phase,
    payload,
    evidence,
    freeze_record,
    freeze_completed,
    frozen_sha,
    frozen_tree,
    pin_ref,
    attempt_count,
    available_at,
    lease_owner,
    lease_token,
    lease_until,
    claimed_from,
    blocked_domain,
    blocked_lock_key,
    blocked_holder,
    blocked_operation,
    wait_started_at,
    last_error_code,
    retryable,
    cancel_requested,
    release_intent,
    reconcile_claimed,
    reconcile_reason,
    recovery_since,
    phase_note,
    requested_by,
    result,
    created_at,
    updated_at,
    finished_at,
    touch_seq
)
SELECT
    job_id,
    kind,
    project_id,
    group_id,
    target_key,
    request_key,
    request_key_hash,
    request_fingerprint,
    has_freeze_phase,
    status,
    phase,
    payload,
    evidence,
    freeze_record,
    freeze_completed,
    frozen_sha,
    frozen_tree,
    pin_ref,
    attempt_count,
    available_at,
    lease_owner,
    lease_token,
    lease_until,
    claimed_from,
    blocked_domain,
    blocked_lock_key,
    blocked_holder,
    blocked_operation,
    wait_started_at,
    last_error_code,
    retryable,
    cancel_requested,
    release_intent,
    reconcile_claimed,
    reconcile_reason,
    recovery_since,
    phase_note,
    requested_by,
    result,
    created_at,
    updated_at,
    finished_at,
    touch_seq
FROM operation_job;

DROP TABLE operation_job;
ALTER TABLE operation_job_new RENAME TO operation_job;

CREATE UNIQUE INDEX idx_job_request ON operation_job (project_id, kind, request_key_hash);
CREATE INDEX idx_job_claim ON operation_job (status, available_at, project_id, created_at);
CREATE INDEX idx_job_lease ON operation_job (status, lease_until);
CREATE INDEX idx_job_lease_owner ON operation_job (lease_owner);
CREATE INDEX idx_job_blocked ON operation_job (blocked_lock_key, status, created_at);
CREATE INDEX idx_job_group ON operation_job (group_id, kind, status);
COMMIT;
PRAGMA foreign_keys=ON;
