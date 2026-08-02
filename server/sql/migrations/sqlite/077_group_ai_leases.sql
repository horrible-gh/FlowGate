-- 077_group_ai_leases.sql — flowgate.default.0378
BEGIN;
CREATE TABLE IF NOT EXISTS group_ai_leases (
    group_id       TEXT PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
    project_id     TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    run_id         TEXT NOT NULL,
    chain_id       TEXT,
    token_id       TEXT,
    action_scope   TEXT NOT NULL,
    worker_identity TEXT,
    state          TEXT NOT NULL CHECK (state IN ('acquiring', 'active', 'releasing')),
    generation     INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
    acquired_at    TEXT NOT NULL,
    heartbeat_at   TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_group_ai_leases_run ON group_ai_leases(run_id);
CREATE INDEX IF NOT EXISTS idx_group_ai_leases_project ON group_ai_leases(project_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_group_ai_leases_expiry ON group_ai_leases(expires_at);
COMMIT;