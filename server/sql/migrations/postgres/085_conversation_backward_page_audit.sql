-- 085_conversation_backward_page_audit.sql
-- flowgate.default.0438: record scroll-up delivery without advancing read cursors.
BEGIN;
CREATE TABLE IF NOT EXISTS conversation_backward_page_audit (
    id              BIGSERIAL PRIMARY KEY,
    doc_id          TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    participant_key TEXT    NOT NULL,
    actor_kind      TEXT    NOT NULL,
    before_seq      INTEGER NOT NULL CHECK (before_seq >= 1),
    returned_count  INTEGER NOT NULL CHECK (returned_count >= 0),
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_backward_audit_doc_created
    ON conversation_backward_page_audit(doc_id, created_at DESC);
COMMIT;