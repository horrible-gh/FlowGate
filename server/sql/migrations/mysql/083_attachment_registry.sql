-- 083_attachment_registry.sql
-- flowgate.default.0060 DB0013 §2·§3 (D0010 §3-1 / P0011 §1-3 / L0012 §2-1~§2-12):
-- the document attachment registry. Until now an upload wrote a file into a side tree and
-- left no record at all, so nothing could list, download or delete it (NR0003 G2·G3).
--
-- Column widths follow DB0013 §2-3: VARCHAR(255) for the two name columns and VARCHAR(512)
-- for file_path, so the composite unique key stays inside InnoDB's key-length budget.
--
-- Numbering: main has taken 080 twice and 081/082 as well since this branch was cut, so this
-- file sits at 083 — the first free ordinal; re-application must stay a no-op wherever it
-- lands (DB0013 §3-1 warning). MySQL has
-- no `CREATE INDEX IF NOT EXISTS`, so the five indexes are declared INSIDE the
-- `CREATE TABLE IF NOT EXISTS` as KEY/UNIQUE KEY clauses. Same index names, same columns,
-- same order as the postgres/sqlite dialects — and the whole statement becomes a no-op on a
-- database that already has the table, which a bare `CREATE INDEX` would not.
--
-- No back-fill here — the legacy tree can only be found by directory listing and the copy
-- is heavy I/O that must not block start-up (DB0013 §3-4, L0012 §2-11).
--
-- One deliberate deviation from the DB0013 §3-1 MySQL block: `content_type` carries no
-- column DEFAULT. MySQL rejects a literal DEFAULT on a TEXT column before 8.0.13, and the
-- value is never omitted — L0012 §2-3 E2/E4 always resolves a media type (falling back to
-- `application/octet-stream`) before the row is written. The column stays NOT NULL, so the
-- invariant DB0013 §5-1 names is unchanged.

SET FOREIGN_KEY_CHECKS=0;

CREATE TABLE IF NOT EXISTS attachments (
    id                  INTEGER      NOT NULL AUTO_INCREMENT,
    doc_id              VARCHAR(191) NOT NULL,
    original_filename   VARCHAR(255) NOT NULL,
    filename            VARCHAR(255) NOT NULL,
    file_path           VARCHAR(512) NOT NULL,
    size                BIGINT       NOT NULL,
    content_type        TEXT         NOT NULL,
    content_sha256      VARCHAR(64)  NOT NULL,
    uploaded_by         VARCHAR(191) NULL,
    uploaded_at         TEXT         NOT NULL,
    is_legacy_migrated  INTEGER      NOT NULL DEFAULT 0,
    created_at          TEXT         NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY ux_attachments_doc_filename (doc_id, filename),
    UNIQUE KEY ux_attachments_file_path (file_path),
    KEY idx_attachments_doc_uploaded (doc_id, uploaded_at, filename),
    KEY idx_attachments_doc_sha (doc_id, content_sha256),
    KEY idx_attachments_uploaded_by (uploaded_by),
    CONSTRAINT fk_attachments_doc FOREIGN KEY (doc_id)
        REFERENCES documents(doc_id) ON DELETE CASCADE,
    CONSTRAINT fk_attachments_uploader FOREIGN KEY (uploaded_by)
        REFERENCES users(user_id),
    CONSTRAINT ck_attachments_size CHECK (size >= 0),
    CONSTRAINT ck_attachments_sha CHECK (length(content_sha256) = 64),
    CONSTRAINT ck_attachments_relpath CHECK (file_path NOT LIKE '/%')
) ROW_FORMAT=DYNAMIC;

SET FOREIGN_KEY_CHECKS=1;
