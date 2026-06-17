SET FOREIGN_KEY_CHECKS=0;
-- 024_m026_review_workflow.sql
-- M026 Phase 0b — add the VR workflow step + add document review columns to documents
-- Add 'VR' to the workflow_sequence_items.type CHECK (SQLite recreation required)

CREATE TABLE workflow_sequence_items_new (
    id          INTEGER PRIMARY KEY AUTO_INCREMENT,
    sequence_id INTEGER NOT NULL REFERENCES workflow_sequences(id) ON DELETE CASCADE,
    item_seq    INTEGER NOT NULL,
    type        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    doc_class   TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL,
    status      VARCHAR(191)    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'done')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (sequence_id, item_seq)
);
INSERT INTO workflow_sequence_items_new SELECT * FROM workflow_sequence_items;
DROP TABLE workflow_sequence_items;
ALTER TABLE workflow_sequence_items_new RENAME TO workflow_sequence_items;
-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_wfseq_items_seq_order
    ON workflow_sequence_items (sequence_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_wfseq_items_seq_status_order
    ON workflow_sequence_items (sequence_id, status, sort_order);
-- Add document review columns to documents
ALTER TABLE documents ADD COLUMN doc_review_status TEXT
    CHECK (doc_review_status IN ('pending_review','approved','rejected','revised'));
ALTER TABLE documents ADD COLUMN rejection_reason TEXT;
SET FOREIGN_KEY_CHECKS=1;
