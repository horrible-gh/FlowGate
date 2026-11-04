-- 0126 T#1: additive human document decisions and durable idempotency receipts.
-- No badge backfill: existing approved documents are not human decisions.
-- Re-running is safe; rollback code only, retain the audit tables.
CREATE TABLE IF NOT EXISTS graph_document_decision_requests (
    request_id VARCHAR(191) NOT NULL PRIMARY KEY,
    group_id VARCHAR(191) NOT NULL,
    node_key VARCHAR(191) NOT NULL,
    request_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    decision_version BIGINT NOT NULL,
    actor_id VARCHAR(191) NOT NULL,
    context_json LONGTEXT NOT NULL,
    result_json LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE (group_id, node_key, request_key),
    UNIQUE (group_id, node_key, decision_version)
);
CREATE TABLE IF NOT EXISTS graph_document_decisions (
    decision_id VARCHAR(191) NOT NULL PRIMARY KEY,
    request_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    node_key VARCHAR(191) NOT NULL,
    slot VARCHAR(32) NOT NULL,
    doc_id VARCHAR(191) NOT NULL,
    revision_no BIGINT NOT NULL,
    document_mark_json LONGTEXT NOT NULL,
    decision_version BIGINT NOT NULL,
    action VARCHAR(32) NOT NULL,
    before_state VARCHAR(32) ,
    after_state VARCHAR(32) NOT NULL,
    actor_kind VARCHAR(32) NOT NULL,
    actor_id VARCHAR(191) NOT NULL,
    cause VARCHAR(80) NOT NULL,
    reason VARCHAR(80) ,
    request_key VARCHAR(128) NOT NULL,
    cascade_from VARCHAR(191) ,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE (group_id, node_key, slot, decision_version)
);
