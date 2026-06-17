-- 043_document_type_template_contents.sql
-- flowgate.default.0024.0015-T  (design: D0010 / P0011 / P0012 / L0013 / DB0014)
-- Stage 3-1 (DB0014 §3-1): additive, zero-risk creation of the template-body table.
--   Holds one markdown body per (template_id, locale) — the D안 (DB-resident body)
--   that replaces the old file-path pointer for design-document template provision.
-- Rollback = DROP TABLE document_type_template_contents (loses ingested bodies only).

PRAGMA foreign_keys = OFF;
BEGIN;

-- ── Template body, one row per (registry row, locale) ────────────────────────
CREATE TABLE IF NOT EXISTS document_type_template_contents (
    template_id  INTEGER NOT NULL
        REFERENCES document_type_templates(id) ON DELETE CASCADE,
    locale       TEXT    NOT NULL,            -- ISO 639-1: ko / ja / en (fallback = ko)
    content      TEXT    NOT NULL,            -- markdown body; 1..512000 byte enforced by app (P0011 §5)
    updated_by   TEXT    REFERENCES users(user_id),   -- NULL allowed (system / AI ingest)
    updated_at   TEXT    NOT NULL,            -- ISO8601 string (matches existing *_at convention)
    PRIMARY KEY (template_id, locale)
);

-- Optional cross-template locale scan (mgmt dashboards). Hot paths enter by
-- template_id and are already covered by the PK; this is a low-priority index.
CREATE INDEX IF NOT EXISTS idx_dttc_locale
    ON document_type_template_contents(locale);

COMMIT;
PRAGMA foreign_keys = ON;
