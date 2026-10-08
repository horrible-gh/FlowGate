-- 132_git_concurrency_resource_lock.sql
-- flowgate.default.0669 (단위 1a) / 0666 0010-DB §2.1·§2.2·§3 (설계 번호 131 → 132).
-- resource_lock        — domain(P/G/W/B/R/M) 잠금 점유의 정본. 획득 = lock_key PK 삽입(L 2.5).
--                        group_id 는 G 만, target_key 는 W 만 채운다(CHECK). 진단용 원문이라 FK 없음(DB §2.0).
-- resource_reservation — fairness 예약(L 2.12). lock 행 없이도 존재할 수 있어 resource_lock 에 FK 를 걸지 않는다.
-- 가산형.

CREATE TABLE resource_lock (
    lock_key        VARCHAR(66)  NOT NULL PRIMARY KEY,
    domain          VARCHAR(1)   NOT NULL CHECK (domain IN ('P', 'G', 'W', 'B', 'R', 'M')),
    project_id      VARCHAR(191) NOT NULL,
    group_id        VARCHAR(191) NULL,
    target_key      VARCHAR(191) NULL,
    holder_ctx_id   VARCHAR(120) NOT NULL,
    holder_kind     VARCHAR(32)  NOT NULL CHECK (holder_kind IN ('selfcheck', 'bundle', 'tr2_apply', 'source_mutation', 'tr_commit', 'time_machine', 'tr_conflict', 'approval_freeze', 'publish', 'worktree_provision', 'worktree_cleanup', 'base_mutation', 'branch_meta', 'branch_merge', 'archive', 'rerere', 'merge_resolve', 'review_action', 'project_provision', 'sweeper', 'legacy_bridge')),
    job_id          VARCHAR(30)  NULL,
    instance_id     VARCHAR(37)  NOT NULL,
    lock_epoch      VARCHAR(27)  NOT NULL,
    hold_class      VARCHAR(5)   NOT NULL CHECK (hold_class IN ('short', 'long')),
    acquired_at     VARCHAR(25)  NOT NULL,
    heartbeat_until VARCHAR(25)  NULL,
    protected       INTEGER      NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
    protect_reason  VARCHAR(40)  NULL,
    protected_at    VARCHAR(25)  NULL,
    exec_state      VARCHAR(8)   NULL CHECK (exec_state IS NULL OR exec_state IN ('spawning', 'running')),
    exec_pid        INTEGER      NULL,
    exec_started_at VARCHAR(25)  NULL,
    exec_seq        INTEGER      NOT NULL DEFAULT 0,
    CHECK (domain <> 'G' OR group_id IS NOT NULL),
    CHECK (domain <> 'W' OR target_key IS NOT NULL),
    CONSTRAINT fk_rlock_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_rlock_job FOREIGN KEY (job_id) REFERENCES operation_job(job_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rlock_instance FOREIGN KEY (instance_id) REFERENCES server_instance(instance_id) ON DELETE RESTRICT
);

CREATE INDEX idx_resource_lock_instance ON resource_lock (instance_id);
CREATE INDEX idx_resource_lock_scope ON resource_lock (project_id, group_id);
CREATE INDEX idx_resource_lock_sweep_long ON resource_lock (hold_class, protected, heartbeat_until);
CREATE INDEX idx_resource_lock_sweep_short ON resource_lock (hold_class, protected, acquired_at);
CREATE INDEX idx_resource_lock_job ON resource_lock (job_id);

CREATE TABLE resource_reservation (
    lock_key          VARCHAR(66) NOT NULL PRIMARY KEY,
    job_id            VARCHAR(30) NULL,
    first_reserved_at VARCHAR(25) NULL,
    reserved_at       VARCHAR(25) NULL,
    expires_at        VARCHAR(25) NULL,
    cooldown_until    VARCHAR(25) NULL,
    CONSTRAINT fk_reservation_job FOREIGN KEY (job_id) REFERENCES operation_job(job_id) ON DELETE RESTRICT
);

CREATE INDEX idx_reservation_job ON resource_reservation (job_id);
