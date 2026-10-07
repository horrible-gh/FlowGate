-- 140_source_bundle_retirement.sql
-- flowgate.default.0684 T#4 (D#1 §2-2, §7): Source Bundle final removal.
--   1. Drop the five Bundle tables. Children first: pins/usages/scratches reference
--      source_bundles; builds stands alone. Bundle rows, build/usage/Scratch history and
--      Retention Pins have no remaining reader. The on-disk source-bundles/ directories
--      are removed once at startup (startup.remove_retired_source_bundle_storage).
--   2. resource_lock.holder_kind: the long Group hold a spec run's preparing copy takes
--      (holder run_prepare, 0684 T#2) was stored under the Bundle label 'bundle'. It is
--      now stored as itself and 'bundle' leaves the CHECK; a row left by the prior
--      process is relabelled, not dropped.
-- TS meta test_basis / superseded_test_basis (v1/v2 Basis, Bundle binding included) is
-- document metadata, not a table here, and stays untouched (D#1 §7).
PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS source_bundle_pins;
DROP TABLE IF EXISTS source_bundle_usages;
DROP TABLE IF EXISTS source_bundle_scratches;
DROP TABLE IF EXISTS source_bundle_builds;
DROP TABLE IF EXISTS source_bundles;

ALTER TABLE resource_lock RENAME TO resource_lock_before_140;

CREATE TABLE resource_lock (
    lock_key        VARCHAR(66)  NOT NULL PRIMARY KEY,
    domain          VARCHAR(1)   NOT NULL CHECK (domain IN ('P', 'G', 'W', 'B', 'R', 'M')),
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    group_id        TEXT NULL,
    target_key      TEXT NULL,
    holder_ctx_id   VARCHAR(120) NOT NULL,
    holder_kind     VARCHAR(32)  NOT NULL CHECK (holder_kind IN ('selfcheck', 'run_prepare', 'tr2_apply', 'source_mutation', 'tr_commit', 'time_machine', 'tr_conflict', 'approval_freeze', 'publish', 'worktree_provision', 'worktree_cleanup', 'base_mutation', 'branch_meta', 'branch_merge', 'archive', 'rerere', 'merge_resolve', 'review_action', 'project_provision', 'sweeper', 'legacy_bridge')),
    job_id          VARCHAR(30)  NULL REFERENCES operation_job(job_id) ON DELETE RESTRICT,
    instance_id     VARCHAR(37)  NOT NULL REFERENCES server_instance(instance_id) ON DELETE RESTRICT,
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
    CHECK (domain <> 'W' OR target_key IS NOT NULL)
);

INSERT INTO resource_lock (
    lock_key, domain, project_id, group_id, target_key, holder_ctx_id, holder_kind, job_id,
    instance_id, lock_epoch, hold_class, acquired_at, heartbeat_until, protected,
    protect_reason, protected_at, exec_state, exec_pid, exec_started_at, exec_seq
)
SELECT
    lock_key, domain, project_id, group_id, target_key, holder_ctx_id,
    CASE WHEN holder_kind = 'bundle' THEN 'run_prepare' ELSE holder_kind END, job_id,
    instance_id, lock_epoch, hold_class, acquired_at, heartbeat_until, protected,
    protect_reason, protected_at, exec_state, exec_pid, exec_started_at, exec_seq
FROM resource_lock_before_140;

DROP TABLE resource_lock_before_140;

CREATE INDEX idx_resource_lock_instance ON resource_lock (instance_id);
CREATE INDEX idx_resource_lock_scope ON resource_lock (project_id, group_id);
CREATE INDEX idx_resource_lock_sweep_long ON resource_lock (hold_class, protected, heartbeat_until);
CREATE INDEX idx_resource_lock_sweep_short ON resource_lock (hold_class, protected, acquired_at);
CREATE INDEX idx_resource_lock_job ON resource_lock (job_id);
COMMIT;
PRAGMA foreign_keys=ON;
