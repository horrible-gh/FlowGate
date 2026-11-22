-- Per-conversation read-chat leases and immutable admission observations (0067).
CREATE TABLE IF NOT EXISTS chat_ai_leases (
    doc_id VARCHAR(255) PRIMARY KEY,
    group_id VARCHAR(255) NOT NULL,
    project_id VARCHAR(255) NOT NULL,
    run_id VARCHAR(255) NOT NULL UNIQUE,
    chain_id VARCHAR(255),
    token_id VARCHAR(255),
    action_scope VARCHAR(255) NOT NULL,
    worker_identity VARCHAR(255),
    state VARCHAR(255) NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    acquired_at VARCHAR(255) NOT NULL,
    heartbeat_at VARCHAR(255) NOT NULL,
    expires_at VARCHAR(255) NOT NULL,
    updated_at VARCHAR(255) NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_run_observations (
    run_id VARCHAR(255) PRIMARY KEY,
    doc_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    observed_during_card_run INTEGER NOT NULL DEFAULT 0
);
