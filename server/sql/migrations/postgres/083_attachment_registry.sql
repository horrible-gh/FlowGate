-- 083_attachment_registry.sql
-- flowgate.default.0060 DB0013 §2·§3 (D0010 §3-1 / P0011 §1-3 / L0012 §2-1~§2-12):
-- the document attachment registry. Until now an upload wrote a file into a side tree and
-- left no record at all, so nothing could list, download or delete it (NR0003 G2·G3).
-- `attachments` is the single persistent store every registry_* call in L0012 reads and
-- writes; the physical file lives next to the document body (D0010 §3-1).
--
-- Additive only: one new leaf table plus its five indexes. No existing table is touched,
-- and nothing yet references `attachments`, so `DROP TABLE attachments` is a complete
-- rollback (DB0013 §3-3).
--
-- Numbering: main has taken 080 twice (080_ai_invoke_prompt_audit /
-- 080_workflow_sequence_provider) and 081/082 as well since this branch was cut, so this file
-- sits at 083 — the first free ordinal. Everything is `IF NOT EXISTS`, which makes
-- re-application a no-op no matter where the file lands in the order (DB0013 §3-1 warning).
--
-- No back-fill here — the legacy tree can only be found by directory listing and the copy
-- is heavy I/O that must not block start-up (DB0013 §3-4, L0012 §2-11).

BEGIN;

CREATE TABLE IF NOT EXISTS attachments (
    id                  SERIAL  PRIMARY KEY,
    doc_id              TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    original_filename   TEXT    NOT NULL,
    filename            TEXT    NOT NULL,
    file_path           TEXT    NOT NULL,
    size                BIGINT  NOT NULL CHECK (size >= 0),
    content_type        TEXT    NOT NULL DEFAULT 'application/octet-stream',
    content_sha256      TEXT    NOT NULL CHECK (length(content_sha256) = 64),
    uploaded_by         TEXT    REFERENCES users(user_id),
    uploaded_at         TEXT    NOT NULL,
    is_legacy_migrated  INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    CHECK (file_path NOT LIKE '/%')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_attachments_doc_filename  ON attachments(doc_id, filename);
CREATE UNIQUE INDEX IF NOT EXISTS ux_attachments_file_path     ON attachments(file_path);
CREATE INDEX        IF NOT EXISTS idx_attachments_doc_uploaded ON attachments(doc_id, uploaded_at, filename);
CREATE INDEX        IF NOT EXISTS idx_attachments_doc_sha      ON attachments(doc_id, content_sha256);
CREATE INDEX        IF NOT EXISTS idx_attachments_uploaded_by  ON attachments(uploaded_by);

COMMIT;
