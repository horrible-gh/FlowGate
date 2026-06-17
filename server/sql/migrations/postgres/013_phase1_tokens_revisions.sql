-- T227 Phase 1 DB migration
-- Basis: D020 r2 §5-1, §5-2, §5-4
-- Applied: add tokens / add documents.revision_no / add document_revisions

-- §5-1: add the tokens table
CREATE TABLE IF NOT EXISTS tokens (
    token_id     TEXT PRIMARY KEY,
    hash         TEXT NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      TEXT NOT NULL REFERENCES projects(project_id),
    group_id     TEXT REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL CHECK (action_scope IN ('new', 'edit')),
    issued_to    TEXT NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    revoked_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tokens_hash        ON tokens(hash);
CREATE INDEX        IF NOT EXISTS idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX        IF NOT EXISTS idx_tokens_issued_to  ON tokens(issued_to);
CREATE INDEX        IF NOT EXISTS idx_tokens_project    ON tokens(project);
-- §5-2: add the documents.revision_no column and backfill
ALTER TABLE documents ADD COLUMN revision_no INTEGER NOT NULL DEFAULT 0;
UPDATE documents SET revision_no = 0 WHERE revision_no IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_revision ON documents(doc_id, revision_no);
-- §5-4: add the document_revisions table
CREATE TABLE IF NOT EXISTS document_revisions (
    id           SERIAL PRIMARY KEY,
    doc_id       TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL,
    backup_path  TEXT    NOT NULL,
    edit_reason  TEXT    NOT NULL
                     CHECK (edit_reason IN ('rejected','qna_followup','user_comment','worker_self')),
    linked_doc_id TEXT,
    created_by   TEXT    NOT NULL REFERENCES users(user_id),
    created_at   TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE INDEX IF NOT EXISTS idx_doc_revisions_created_at ON document_revisions(created_at);
CREATE INDEX IF NOT EXISTS idx_doc_revisions_doc_id     ON document_revisions(doc_id, revision_no DESC);