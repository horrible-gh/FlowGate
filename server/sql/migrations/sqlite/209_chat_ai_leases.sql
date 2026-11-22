-- Per-conversation read-chat leases and immutable admission observations (0067).
CREATE TABLE IF NOT EXISTS chat_ai_leases (
    doc_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    chain_id TEXT,
    token_id TEXT,
    action_scope TEXT NOT NULL,
    worker_identity TEXT,
    state TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_run_observations (
    run_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    observed_during_card_run INTEGER NOT NULL DEFAULT 0
);
