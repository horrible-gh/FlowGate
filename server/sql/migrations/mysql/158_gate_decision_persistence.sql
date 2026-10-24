-- 158_gate_decision_persistence.sql — flowgate.v02.0085 T#1 (D0007 §7.1, §7.2 persistent data) (MySQL/MariaDB)
-- Durable records of a confirm gate's decisions, the effects each decision had, the archive checkpoints and the
-- cleanup records, plus the per-card settings version and the one-time move of the old ``merge_target`` values.
-- Additive: nothing here changes a run, a node state or a stored policy snapshot.
--   gate_decisions            one row per accepted decision intent. Request key unique (run, node, actor, request id);
--                             at most one decision per attempt except a canceled one (valid_attempt_key is the attempt
--                             id while the decision counts and NULL once canceled, like graph_node_attempts.
--                             active_node_key); decision_version, close kind / closer / reason / time and
--                             recovery_reset_required are kept on the row.
--   gate_decision_effects     one row per (decision, phase): the evidence each phase left, unknown effects, the
--                             evidence version a closer reviewed and the non-consuming recovery ref.
--   gate_archive_checkpoints  one per archive id and one per decision; the group id is NOT a key, so a later gate never
--                             overwrites an earlier checkpoint.
--   gate_cleanup_records      the real HEAD at the end, the basis ref / observed sha, reachability and the outcome.
--   graph_node_settings_version  the version a settings write moves; a gate settings PATCH is checked against it.
--   gate_data_migrations      one row per one-time data move, so a re-run never moves data twice.
-- History is never removed by a cascade: the gate tables have no REFERENCES to groups, runs or attempts (a run,
-- attempt or Flow delete leaves them), and effects / checkpoints point at their decision with ON DELETE RESTRICT.

CREATE TABLE IF NOT EXISTS gate_data_migrations (
    name        VARCHAR(100) NOT NULL PRIMARY KEY,
    applied_at  VARCHAR(40) NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_node_settings_version (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at  VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, node_key),
    CONSTRAINT fk_graph_node_settings_version_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id                VARCHAR(40) NOT NULL PRIMARY KEY,
    group_id                   VARCHAR(191) NOT NULL,
    run_id                     VARCHAR(64) NOT NULL,
    node_key                   VARCHAR(200) NOT NULL,
    attempt_id                 VARCHAR(64) NOT NULL,
    valid_attempt_key          VARCHAR(64) NULL,
    revision_no                INTEGER NOT NULL,
    policy_version             VARCHAR(64) NULL,
    actor_user_id              VARCHAR(191) NOT NULL,
    request_id                 VARCHAR(128) NOT NULL,
    request_fingerprint        CHAR(64) NOT NULL,
    decision                   VARCHAR(16) NOT NULL CHECK (decision IN ('confirm', 'reject')),
    decision_comment           MEDIUMTEXT NULL,
    status                     VARCHAR(24) NOT NULL CHECK (status IN ('accepted', 'failed', 'recovery_required', 'succeeded', 'rejected', 'canceled', 'abandoned')),
    decision_version           INTEGER NOT NULL DEFAULT 1 CHECK (decision_version >= 1),
    settings_version           INTEGER NULL,
    settings_json              MEDIUMTEXT NULL,
    frozen_sha                 VARCHAR(64) NULL,
    input_attempt_ref          VARCHAR(191) NULL,
    target_remote              VARCHAR(64) NULL,
    target_branch              VARCHAR(255) NULL,
    target_sha                 VARCHAR(64) NULL,
    job_id                     VARCHAR(30) NULL,
    previous_decision_id       VARCHAR(40) NULL,
    superseded_by_decision_id  VARCHAR(40) NULL,
    time_machine_ref           VARCHAR(191) NULL,
    last_error_code            VARCHAR(64) NULL,
    close_kind                 VARCHAR(24) NULL CHECK (close_kind IS NULL OR close_kind IN ('effects_confirmed', 'abandon')),
    closed_by                  VARCHAR(191) NULL,
    close_reason               MEDIUMTEXT NULL,
    closed_at                  VARCHAR(40) NULL,
    recovery_reset_required    INTEGER NOT NULL DEFAULT 0 CHECK (recovery_reset_required IN (0, 1)),
    accepted_at                VARCHAR(40) NOT NULL,
    completed_at               VARCHAR(40) NULL,
    updated_at                 VARCHAR(40) NOT NULL,
    UNIQUE KEY idx_gate_decisions_request (run_id, node_key, actor_user_id, request_id),
    UNIQUE KEY idx_gate_decisions_attempt (valid_attempt_key),
    INDEX idx_gate_decisions_node (group_id, node_key, accepted_at, decision_id),
    INDEX idx_gate_decisions_run (run_id, node_key, status),
    CHECK ((status = 'canceled' AND valid_attempt_key IS NULL) OR (status <> 'canceled' AND valid_attempt_key IS NOT NULL AND valid_attempt_key = attempt_id)),
    CHECK (close_kind IS NULL OR status IN ('succeeded', 'abandoned')),
    CHECK (recovery_reset_required = 0 OR status = 'abandoned')
);

CREATE TABLE IF NOT EXISTS gate_decision_effects (
    decision_id               VARCHAR(40) NOT NULL,
    phase                     VARCHAR(40) NOT NULL,
    status                    VARCHAR(16) NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'unknown', 'skipped')),
    target_ref                VARCHAR(255) NULL,
    sha_before                VARCHAR(64) NULL,
    sha_after                 VARCHAR(64) NULL,
    evidence_json             MEDIUMTEXT NULL,
    archive_id                VARCHAR(64) NULL,
    error_code                VARCHAR(64) NULL,
    recovery_state            VARCHAR(16) NOT NULL DEFAULT 'none' CHECK (recovery_state IN ('none', 'required', 'confirmed', 'unknown', 'abandoned')),
    recovery_ref              VARCHAR(255) NULL,
    attempt_count             INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    evidence_version          INTEGER NOT NULL DEFAULT 1 CHECK (evidence_version >= 1),
    reviewed_evidence_version INTEGER NULL,
    created_at                VARCHAR(40) NOT NULL,
    updated_at                VARCHAR(40) NOT NULL,
    PRIMARY KEY (decision_id, phase),
    CONSTRAINT fk_gate_effect_decision FOREIGN KEY (decision_id) REFERENCES gate_decisions(decision_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS gate_archive_checkpoints (
    archive_id        VARCHAR(64) NOT NULL PRIMARY KEY,
    decision_id       VARCHAR(40) NOT NULL,
    group_id          VARCHAR(191) NOT NULL,
    run_id            VARCHAR(64) NOT NULL,
    node_key          VARCHAR(200) NOT NULL,
    attempt_id        VARCHAR(64) NOT NULL,
    source_sha        VARCHAR(64) NOT NULL,
    applied_sha       VARCHAR(64) NULL,
    ref_name          VARCHAR(255) NOT NULL,
    applied_ref       VARCHAR(255) NULL,
    restore_info      MEDIUMTEXT NULL,
    preserved_at      VARCHAR(40) NOT NULL,
    last_restored_at  VARCHAR(40) NULL,
    UNIQUE KEY idx_gate_archive_decision (decision_id),
    UNIQUE KEY idx_gate_archive_ref (ref_name),
    INDEX idx_gate_archive_group (group_id, preserved_at),
    CONSTRAINT fk_gate_archive_decision FOREIGN KEY (decision_id) REFERENCES gate_decisions(decision_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS gate_cleanup_records (
    cleanup_id      VARCHAR(64) NOT NULL PRIMARY KEY,
    group_id        VARCHAR(191) NOT NULL,
    run_id          VARCHAR(64) NULL,
    job_id          VARCHAR(30) NULL,
    branch_name     VARCHAR(255) NULL,
    end_head_sha    VARCHAR(64) NOT NULL,
    basis_kind      VARCHAR(24) NULL CHECK (basis_kind IS NULL OR basis_kind IN ('archive_checkpoint', 'recovery_ref', 'merged_target')),
    basis_ref       VARCHAR(255) NULL,
    basis_sha       VARCHAR(64) NULL,
    reachable       INTEGER NULL CHECK (reachable IS NULL OR reachable IN (0, 1)),
    keep_requested  INTEGER NOT NULL DEFAULT 0 CHECK (keep_requested IN (0, 1)),
    result          VARCHAR(32) NOT NULL CHECK (result IN ('deleted', 'retained', 'retained_uncovered_head', 'failed')),
    result_reason   VARCHAR(191) NULL,
    evidence_json   MEDIUMTEXT NULL,
    created_at      VARCHAR(40) NOT NULL,
    INDEX idx_gate_cleanup_group (group_id, created_at)
);

-- The old ``merge_target`` was a branch name (a v0.1 value); the new one is a remote (null | origin). Every value
-- stored before this migration moves, as it is, to ``legacy_merge_target`` and the active field goes back to null
-- (no row). The marker row makes the move happen once: an ``origin`` stored AFTER it is the new meaning and a
-- second run leaves it alone. An existing ``legacy_merge_target`` is never overwritten.
INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by)
SELECT s.group_id, s.node_key, 'legacy_merge_target', s.value_json, s.updated_at, s.updated_by
FROM graph_node_settings s
WHERE s.field = 'merge_target'
  AND NOT EXISTS (SELECT 1 FROM gate_data_migrations m WHERE m.name = '158_legacy_merge_target')
  AND NOT EXISTS (SELECT 1 FROM graph_node_settings l
                  WHERE l.group_id = s.group_id AND l.node_key = s.node_key AND l.field = 'legacy_merge_target');
DELETE FROM graph_node_settings
WHERE field = 'merge_target'
  AND NOT EXISTS (SELECT 1 FROM gate_data_migrations m WHERE m.name = '158_legacy_merge_target');
INSERT INTO gate_data_migrations (name, applied_at)
SELECT '158_legacy_merge_target', DATE_FORMAT(UTC_TIMESTAMP(3), '%Y-%m-%dT%H:%i:%s.%fZ') FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM gate_data_migrations m WHERE m.name = '158_legacy_merge_target');
