-- 085_conversation_backward_page_audit.sql
-- flowgate.default.0438: record scroll-up delivery without advancing read cursors.
CREATE TABLE IF NOT EXISTS conversation_backward_page_audit (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    doc_id          VARCHAR(191) NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    participant_key VARCHAR(191) NOT NULL,
    actor_kind      VARCHAR(32)  NOT NULL,
    before_seq      INTEGER      NOT NULL CHECK (before_seq >= 1),
    returned_count  INTEGER      NOT NULL CHECK (returned_count >= 0),
    created_at      VARCHAR(191) NOT NULL,
    INDEX idx_conversation_backward_audit_doc_created (doc_id, created_at DESC)
);