-- 033_drop_wsi_status.sql
-- T630 — D030 §2 SSOT enforcement: drop legacy status column from workflow_sequence_items
--
-- Prerequisites:
--   - Migration 032 (result_doc_id backfill) applied
--   - T630 Python code changes deployed (all legacy slot-status references removed)
--
-- Steps:
--   1. Backfill M slot result_doc_id (idempotent safety net)
--   2. Backfill M documents to doc_review_status='approved'
--   3. Drop idx_wfseq_items_seq_status_order index
--   4. Recreate workflow_sequence_items without status column

PRAGMA foreign_keys = OFF;
BEGIN;

-- ── Step 1: Backfill M slot result_doc_id ──────────────────────────────────────
-- Idempotent: only NULL result_doc_id M slots are targeted.
UPDATE workflow_sequence_items
SET result_doc_id = (
    SELECT d.doc_id
    FROM documents d
    JOIN workflow_sequences ws ON ws.id = workflow_sequence_items.sequence_id
    JOIN documents parent_doc ON parent_doc.doc_id = ws.doc_id
    WHERE d.group_id = parent_doc.group_id
      AND d.type_code = 'M'
    ORDER BY d.created_at DESC
    LIMIT 1
),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE workflow_sequence_items.type = 'M'
  AND workflow_sequence_items.result_doc_id IS NULL
  AND EXISTS (
    SELECT 1
    FROM workflow_sequences ws2
    JOIN documents parent_doc2 ON parent_doc2.doc_id = ws2.doc_id
    JOIN documents d2 ON d2.group_id = parent_doc2.group_id
    WHERE ws2.id = workflow_sequence_items.sequence_id
      AND d2.type_code = 'M'
  );

-- ── Step 2: Backfill M documents to doc_review_status='approved' ──────────────
-- Idempotent: only M docs with NULL/empty doc_review_status are targeted.
UPDATE documents
SET doc_review_status = 'approved',
    updated_at        = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE type_code = 'M'
  AND (doc_review_status IS NULL OR doc_review_status = '')
  AND EXISTS (
    SELECT 1 FROM workflow_sequence_items wsi
    WHERE wsi.result_doc_id = documents.doc_id
  );

-- ── Step 3: Drop composite status index ──────────────────────────────────────
DROP INDEX IF EXISTS idx_wfseq_items_seq_status_order;

-- ── Step 4: Recreate workflow_sequence_items without status column ─────────────
CREATE TABLE workflow_sequence_items_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id   INTEGER NOT NULL REFERENCES workflow_sequences(id) ON DELETE CASCADE,
    item_seq      INTEGER NOT NULL,
    type          TEXT    NOT NULL,
    label         TEXT    NOT NULL,
    doc_class     TEXT    NOT NULL,
    sort_order    INTEGER NOT NULL,
    result_doc_id TEXT    DEFAULT NULL
                      REFERENCES documents(doc_id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (sequence_id, item_seq)
);

INSERT INTO workflow_sequence_items_new
    (id, sequence_id, item_seq, type, label, doc_class, sort_order,
     result_doc_id, created_at, updated_at)
SELECT
    id, sequence_id, item_seq, type, label, doc_class, sort_order,
    result_doc_id, created_at, updated_at
FROM workflow_sequence_items;

DROP TABLE workflow_sequence_items;
ALTER TABLE workflow_sequence_items_new RENAME TO workflow_sequence_items;

-- ── Recreate indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_wfseq_items_seq_order
    ON workflow_sequence_items (sequence_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_wfseq_items_result_doc
    ON workflow_sequence_items (result_doc_id)
    WHERE result_doc_id IS NOT NULL;

COMMIT;
PRAGMA foreign_keys = ON;

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- To roll back: restore from backup
--   cp server/flowgate.db.pre_033_backup server/flowgate.db
-- (SQLite column drops are not reversible via SQL; use backup/restore)
--
-- Pre-migration backup command:
--   cp server/flowgate.db server/flowgate.db.pre_033_backup
