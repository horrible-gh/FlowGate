-- 131_git_concurrency_operation_job.sql
-- flowgate.default.0669 (단위 1a) / 0666 0010-DB §2.3·§3 (설계 번호 130 → 131).
-- durable job 대기열(operation_job). 0669 범위는 job 을 쓰지 않는다 — resource_lock.job_id 의
-- FK 대상이라 DB 설계 그대로 테이블만 만들어 둔다(0669 대화 seq 18, 단위 1a).
-- 가산형. JSON 컬럼은 TEXT 에 UTF-8 JSON(DB §2.0).

CREATE TABLE operation_job (
    job_id              VARCHAR(30)  NOT NULL PRIMARY KEY,
    kind                VARCHAR(32)  NOT NULL CHECK (kind IN ('final_approval_publish', 'branch_merge_publish', 'worktree_provision', 'worktree_cleanup', 'base_publish', 'archive_preserve', 'archive_restore', 'archive_purge', 'project_provision')),
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

CREATE UNIQUE INDEX idx_job_request ON operation_job (project_id, kind, request_key_hash);
CREATE INDEX idx_job_claim ON operation_job (status, available_at, project_id, created_at);
CREATE INDEX idx_job_lease ON operation_job (status, lease_until);
CREATE INDEX idx_job_lease_owner ON operation_job (lease_owner);
CREATE INDEX idx_job_blocked ON operation_job (blocked_lock_key, status, created_at);
