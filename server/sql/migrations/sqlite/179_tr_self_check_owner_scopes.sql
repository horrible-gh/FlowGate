-- 0146 T#3: admission fence and durable cancellation intent. No historical backfill.
CREATE TABLE tr_self_check_owner_scopes (
    scope_id TEXT PRIMARY KEY,
    ai_run_id TEXT NOT NULL,
    hop_id TEXT NOT NULL,
    provider_attempt_id TEXT NOT NULL,
    owner_token_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    closed_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    close_reason TEXT
);
CREATE INDEX idx_selfcheck_owner_parent ON tr_self_check_owner_scopes(ai_run_id, hop_id, provider_attempt_id);
CREATE INDEX idx_selfcheck_owner_token ON tr_self_check_owner_scopes(owner_token_id);
