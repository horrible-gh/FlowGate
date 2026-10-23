-- 158_gate_decision_persistence.sql — flowgate.v02.0085 T#1 (D0007 §7.1, §7.2 persistent data) (PostgreSQL)
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
    name        TEXT NOT NULL PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_node_settings_version (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, node_key)
);

CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id                TEXT NOT NULL PRIMARY KEY,
    group_id                   TEXT NOT NULL,
    run_id                     TEXT NOT NULL,
    node_key                   TEXT NOT NULL,
    attempt_id                 TEXT NOT NULL,
    valid_attempt_key          TEXT NULL,
    revision_no                INTEGER NOT NULL,
    policy_version             TEXT NULL,
    actor_user_id              TEXT NOT NULL,
    request_id                 TEXT NOT NULL,
    request_fingerprint        CHAR(64) NOT NULL,
    decision                   TEXT NOT NULL CHECK (decision IN ('confirm', 'reject')),
    decision_comment           TEXT NULL,
    status                     TEXT NOT NULL CHECK (status IN ('accepted', 'failed', 'recovery_required', 'succeeded', 'rejected', 'canceled', 'abandoned')),
    decision_version           INTEGER NOT NULL DEFAULT 1 CHECK (decision_version >= 1),
    settings_version           INTEGER NULL,
    settings_json              TEXT NULL,
    frozen_sha                 TEXT NULL,
    input_attempt_ref          TEXT NULL,
    target_remote              TEXT NULL,
    target_branch              TEXT NULL,
    target_sha                 TEXT NULL,
    job_id                     TEXT NULL,
    previous_decision_id       TEXT NULL,
    superseded_by_decision_id  TEXT NULL,
    time_machine_ref           TEXT NULL,
    last_error_code            TEXT NULL,
    close_kind                 TEXT NULL CHECK (close_kind IS NULL OR close_kind IN ('effects_confirmed', 'abandon')),
    closed_by                  TEXT NULL,
    close_reason               TEXT NULL,
    closed_at                  TEXT NULL,
    recovery_reset_required    INTEGER NOT NULL DEFAULT 0 CHECK (recovery_reset_required IN (0, 1)),
    accepted_at                TEXT NOT NULL,
    completed_at               TEXT NULL,
    updated_at                 TEXT NOT NULL,
    CHECK ((status = 'canceled' AND valid_attempt_key IS NULL) OR (status <> 'canceled' AND valid_attempt_key IS NOT NULL AND valid_attempt_key = attempt_id)),
    CHECK (close_kind IS NULL OR status IN ('succeeded', 'abandoned')),
    CHECK (recovery_reset_required = 0 OR status = 'abandoned')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_decisions_request ON gate_decisions (run_id, node_key, actor_user_id, request_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_decisions_attempt ON gate_decisions (valid_attempt_key);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_node ON gate_decisions (group_id, node_key, accepted_at, decision_id);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_run ON gate_decisions (run_id, node_key, status);

CREATE TABLE IF NOT EXISTS gate_decision_effects (
    decision_id               TEXT NOT NULL REFERENCES gate_decisions(decision_id) ON DELETE RESTRICT,
    phase                     TEXT NOT NULL,
    status                    TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'unknown', 'skipped')),
    target_ref                TEXT NULL,
    sha_before                TEXT NULL,
    sha_after                 TEXT NULL,
    evidence_json             TEXT NULL,
    archive_id                TEXT NULL,
    error_code                TEXT NULL,
    recovery_state            TEXT NOT NULL DEFAULT 'none' CHECK (recovery_state IN ('none', 'required', 'confirmed', 'unknown', 'abandoned')),
    recovery_ref              TEXT NULL,
    attempt_count             INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    evidence_version          INTEGER NOT NULL DEFAULT 1 CHECK (evidence_version >= 1),
    reviewed_evidence_version INTEGER NULL,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    PRIMARY KEY (decision_id, phase)
);

CREATE TABLE IF NOT EXISTS gate_archive_checkpoints (
    archive_id        TEXT NOT NULL PRIMARY KEY,
    decision_id       TEXT NOT NULL REFERENCES gate_decisions(decision_id) ON DELETE RESTRICT,
    group_id          TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    node_key          TEXT NOT NULL,
    attempt_id        TEXT NOT NULL,
    source_sha        TEXT NOT NULL,
    applied_sha       TEXT NULL,
    ref_name          TEXT NOT NULL,
    applied_ref       TEXT NULL,
    restore_info      TEXT NULL,
    preserved_at      TEXT NOT NULL,
    last_restored_at  TEXT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_archive_decision ON gate_archive_checkpoints (decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_archive_ref ON gate_archive_checkpoints (ref_name);
CREATE INDEX IF NOT EXISTS idx_gate_archive_group ON gate_archive_checkpoints (group_id, preserved_at);

CREATE TABLE IF NOT EXISTS gate_cleanup_records (
    cleanup_id      TEXT NOT NULL PRIMARY KEY,
    group_id        TEXT NOT NULL,
    run_id          TEXT NULL,
    job_id          TEXT NULL,
    branch_name     TEXT NULL,
    end_head_sha    TEXT NOT NULL,
    basis_kind      TEXT NULL CHECK (basis_kind IS NULL OR basis_kind IN ('archive_checkpoint', 'recovery_ref', 'merged_target')),
    basis_ref       TEXT NULL,
    basis_sha       TEXT NULL,
    reachable       INTEGER NULL CHECK (reachable IS NULL OR reachable IN (0, 1)),
    keep_requested  INTEGER NOT NULL DEFAULT 0 CHECK (keep_requested IN (0, 1)),
    result          TEXT NOT NULL CHECK (result IN ('deleted', 'retained', 'retained_uncovered_head', 'failed')),
    result_reason   TEXT NULL,
    evidence_json   TEXT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gate_cleanup_group ON gate_cleanup_records (group_id, created_at);

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
SELECT '158_legacy_merge_target', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
WHERE NOT EXISTS (SELECT 1 FROM gate_data_migrations m WHERE m.name = '158_legacy_merge_target');
