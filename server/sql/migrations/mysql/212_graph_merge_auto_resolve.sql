-- Durable bounded card-result conflict resolution. No existing data is changed.
-- Rollback: DROP TABLE graph_merge_auto_resolve (loses attempt history).
CREATE TABLE IF NOT EXISTS graph_merge_auto_resolve (
    merge_id BIGINT PRIMARY KEY,
    attempt_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    node_key VARCHAR(191) NOT NULL,
    run_id VARCHAR(191) NOT NULL,
    state VARCHAR(32) NOT NULL,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    owner VARCHAR(64),
    claim_until BIGINT NOT NULL DEFAULT 0
);
