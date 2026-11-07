-- 0146 T#3: admission fence and durable cancellation intent. No historical backfill.
CREATE TABLE tr_self_check_owner_scopes (
    scope_id VARCHAR(191) PRIMARY KEY,
    ai_run_id VARCHAR(191) NOT NULL,
    hop_id VARCHAR(191) NOT NULL,
    provider_attempt_id VARCHAR(191) NOT NULL,
    owner_token_id VARCHAR(191) NOT NULL,
    project_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    closed_at VARCHAR(191),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    close_reason VARCHAR(191)
);
CREATE INDEX idx_selfcheck_owner_parent ON tr_self_check_owner_scopes(ai_run_id, hop_id, provider_attempt_id);
CREATE INDEX idx_selfcheck_owner_token ON tr_self_check_owner_scopes(owner_token_id);
