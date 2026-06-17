-- 035_document_reviews.sql
-- Store AI review results as child records attached to documents, not as a separate document type or series.
-- Flow: action bar [Request review] -> AI inbound(action=review) -> insert into this table ->
--       a person reviews the feedback in the right panel and approves or rejects. Collection is automatic; decisions are manual.

CREATE TABLE IF NOT EXISTS document_reviews (
    id           INTEGER PRIMARY KEY AUTO_INCREMENT,
    doc_id       VARCHAR(191)    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL DEFAULT 0,        -- Revision of the reviewed document
    reviewer_id  TEXT    NOT NULL,                   -- AI worker/system identifier
    verdict      TEXT    NOT NULL DEFAULT 'issues'
                     CHECK (verdict IN ('pass','issues','hold')),
    findings     TEXT    NOT NULL DEFAULT '[]',      -- JSON array: [{`locus`:..,`note`:..}]
    comment      TEXT,                               -- Overall feedback (free text)
    reviewed_at  TEXT    NOT NULL,
    created_at   VARCHAR(191)    NOT NULL DEFAULT (UTC_TIMESTAMP()),
    updated_at   TEXT    NOT NULL DEFAULT (UTC_TIMESTAMP())
);
CREATE INDEX IF NOT EXISTS idx_document_reviews_doc
    ON document_reviews(doc_id, created_at DESC);