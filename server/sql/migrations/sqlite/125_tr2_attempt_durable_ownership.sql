-- 125_tr2_attempt_durable_ownership.sql
-- flowgate.default.0641 D0004 rev2: succeeded TR2 approval attempts are the durable
-- ownership source. A TR2 document or its history ledger may be deleted later, but
-- the succeeded attempt and commit_json.paths must survive.
PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE tr2_approval_attempts RENAME TO tr2_approval_attempts_before_125;

CREATE TABLE tr2_approval_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL UNIQUE,
    request_key TEXT UNIQUE,
    tr2_doc_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    document_revision INTEGER NOT NULL,
    document_etag TEXT,
    approval_round INTEGER NOT NULL,
    actor_user_id TEXT NOT NULL,
    spec_fingerprint TEXT NOT NULL,
    baseline_fingerprint TEXT NOT NULL,
    live_fingerprint TEXT,
    state TEXT NOT NULL CHECK (state IN ('in_progress','succeeded','failed','recovery_required')),
    phase TEXT NOT NULL,
    result_code TEXT,
    error_code TEXT,
    error_detail TEXT,
    precheck_json TEXT,
    apply_json TEXT,
    validation_json TEXT,
    commit_json TEXT,
    ledger_json TEXT,
    backup_bundle_id TEXT,
    pre_apply_head_sha TEXT,
    commit_sha TEXT,
    ledger_row_id INTEGER REFERENCES tr_commit_ledger(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

INSERT INTO tr2_approval_attempts (
    id, attempt_id, request_key, tr2_doc_id, project_id, group_id,
    document_revision, document_etag, approval_round, actor_user_id,
    spec_fingerprint, baseline_fingerprint, live_fingerprint, state, phase,
    result_code, error_code, error_detail, precheck_json, apply_json,
    validation_json, commit_json, ledger_json, backup_bundle_id,
    pre_apply_head_sha, commit_sha, ledger_row_id, started_at, heartbeat_at,
    finished_at, updated_at
)
SELECT
    id, attempt_id, request_key, tr2_doc_id, project_id, group_id,
    document_revision, document_etag, approval_round, actor_user_id,
    spec_fingerprint, baseline_fingerprint, live_fingerprint, state, phase,
    result_code, error_code, error_detail, precheck_json, apply_json,
    validation_json, commit_json, ledger_json, backup_bundle_id,
    pre_apply_head_sha, commit_sha, ledger_row_id, started_at, heartbeat_at,
    finished_at, updated_at
FROM tr2_approval_attempts_before_125;

DROP TABLE tr2_approval_attempts_before_125;

CREATE INDEX IF NOT EXISTS idx_tr2_attempt_doc
    ON tr2_approval_attempts(tr2_doc_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_tr2_attempt_group_state
    ON tr2_approval_attempts(group_id, state);
CREATE INDEX IF NOT EXISTS idx_tr2_attempt_stale
    ON tr2_approval_attempts(state, heartbeat_at);

COMMIT;
PRAGMA foreign_keys=ON;
