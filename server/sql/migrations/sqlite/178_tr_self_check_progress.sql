-- 0146 T#1: immutable provider-attempt/hop ownership and cumulative effective events.
-- No historical runs are backfilled; linked draft runs keep the same ownership row.
CREATE TABLE tr_self_check_run_progress (
    self_check_run_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    ai_run_id TEXT NOT NULL,
    hop_id TEXT NOT NULL,
    provider_attempt_id TEXT NOT NULL,
    owner_token_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    progress_seq BIGINT NOT NULL DEFAULT 1,
    output_bytes BIGINT NOT NULL DEFAULT 0,
    state_rank INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    event_kind TEXT NOT NULL DEFAULT 'created',
    progress_at TEXT NOT NULL,
    FOREIGN KEY (self_check_run_id) REFERENCES tr_self_check_runs(self_check_run_id) ON DELETE CASCADE
);
CREATE INDEX idx_selfcheck_progress_scope ON tr_self_check_run_progress(scope_id);
