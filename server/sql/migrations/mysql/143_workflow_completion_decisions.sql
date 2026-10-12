-- 0428 T#1: independent completion decisions and durable continuation intent.
-- No document review update, legacy rows remain generation 0 with no decision.
-- Audit identities intentionally have no cascading FK to mutable slots/documents.
ALTER TABLE workflow_sequence_items ADD COLUMN slot_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE workflow_sequence_items ADD COLUMN slot_decision_id VARCHAR(64) DEFAULT NULL;

CREATE TABLE workflow_completion_decisions (
    decision_id VARCHAR(64) PRIMARY KEY,
    sequence_id BIGINT NOT NULL,
    slot_id BIGINT NOT NULL,
    slot_generation BIGINT NOT NULL,
    result_doc_id VARCHAR(191) NOT NULL,
    document_revision INTEGER NOT NULL,
    decision_kind VARCHAR(32) NOT NULL CHECK (decision_kind = 'proceeded_unapproved'),
    actor_user_id VARCHAR(191) NOT NULL,
    decided_at VARCHAR(40) NOT NULL,
    invalidated_at VARCHAR(40),
    invalidation_reason TEXT,
    UNIQUE (sequence_id, slot_id, slot_generation)
);
CREATE INDEX idx_completion_decision_doc ON workflow_completion_decisions (result_doc_id);

CREATE TABLE workflow_completion_requests (
    request_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL,
    requested_by VARCHAR(191) NOT NULL,
    requested_at VARCHAR(40) NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES workflow_completion_decisions(decision_id)
);

CREATE TABLE workflow_completion_dispatches (
    dispatch_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL,
    chain_id VARCHAR(128) NOT NULL,
    stop_run_id VARCHAR(128) NOT NULL,
    execution_key VARCHAR(128) NOT NULL UNIQUE,
    state VARCHAR(32) NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','running','completed','retryable_failed','recovery_required','cancelled')),
    state_version BIGINT NOT NULL DEFAULT 0,
    run_id VARCHAR(128),
    token_id VARCHAR(128),
    stop_snapshot_json TEXT NOT NULL,
    error_code VARCHAR(128),
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES workflow_completion_decisions(decision_id),
    UNIQUE (decision_id, chain_id, stop_run_id)
);
CREATE INDEX idx_completion_dispatch_state ON workflow_completion_dispatches (state, updated_at);
