-- 0126 T#1: additive human document decisions and durable idempotency receipts.
-- No badge backfill: existing approved documents are not human decisions.
-- Re-running is safe; rollback code only, retain the audit tables.
CREATE TABLE IF NOT EXISTS graph_document_decision_requests (
    request_id TEXT NOT NULL PRIMARY KEY,
    group_id TEXT NOT NULL,
    node_key TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    decision_version INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    context_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (group_id, node_key, request_key),
    UNIQUE (group_id, node_key, decision_version)
);
CREATE TABLE IF NOT EXISTS graph_document_decisions (
    decision_id TEXT NOT NULL PRIMARY KEY,
    request_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    node_key TEXT NOT NULL,
    slot TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    document_mark_json TEXT NOT NULL,
    decision_version INTEGER NOT NULL,
    action TEXT NOT NULL,
    before_state TEXT ,
    after_state TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    cause TEXT NOT NULL,
    reason TEXT ,
    request_key TEXT NOT NULL,
    cascade_from TEXT ,
    created_at TEXT NOT NULL,
    UNIQUE (group_id, node_key, slot, decision_version)
);
