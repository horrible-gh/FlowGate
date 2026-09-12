-- Durable preflight receipts for mandatory AI review admission.
CREATE TABLE IF NOT EXISTS review_dry_run_receipts (
 receipt_id TEXT PRIMARY KEY,
 token_id TEXT NOT NULL REFERENCES tokens(token_id) ON DELETE CASCADE,
 project_id TEXT NOT NULL REFERENCES projects(project_id),
 group_id TEXT REFERENCES groups(group_id),
 doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
 revision_no INTEGER NOT NULL,
 action_scope TEXT NOT NULL CHECK (action_scope = 'review'),
 payload_identity TEXT NOT NULL,
 issued_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 used_at TEXT,
 superseded_at TEXT,
 encoding_provenance TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_receipts_token ON review_dry_run_receipts(token_id);
CREATE INDEX IF NOT EXISTS idx_review_receipts_target ON review_dry_run_receipts(doc_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_review_receipts_expiry ON review_dry_run_receipts(expires_at);
