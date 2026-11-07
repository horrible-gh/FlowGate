-- 0146 T#1: immutable provider-attempt/hop ownership and cumulative effective events.
-- No historical runs are backfilled; linked draft runs keep the same ownership row.
CREATE TABLE tr_self_check_run_progress (
    self_check_run_id VARCHAR(191) PRIMARY KEY,
    scope_id VARCHAR(191) NOT NULL,
    ai_run_id VARCHAR(191) NOT NULL,
    hop_id VARCHAR(191) NOT NULL,
    provider_attempt_id VARCHAR(191) NOT NULL,
    owner_token_id VARCHAR(191) NOT NULL,
    project_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    progress_seq BIGINT NOT NULL DEFAULT 1,
    output_bytes BIGINT NOT NULL DEFAULT 0,
    state_rank INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(191) NOT NULL DEFAULT 'pending',
    event_kind VARCHAR(191) NOT NULL DEFAULT 'created',
    progress_at VARCHAR(191) NOT NULL,
    FOREIGN KEY (self_check_run_id) REFERENCES tr_self_check_runs(self_check_run_id) ON DELETE CASCADE
);
CREATE INDEX idx_selfcheck_progress_scope ON tr_self_check_run_progress(scope_id);
