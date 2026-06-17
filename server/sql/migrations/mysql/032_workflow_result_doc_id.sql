-- 032_workflow_result_doc_id.sql
-- T600 — DB004 §2: add the result_doc_id column to workflow_sequence_items + backfill
--
-- Step 1: ADD COLUMN (nullable, no downtime)
-- Step 2: Backfill from workflow_item_results (idempotency guaranteed)
-- Step 3: Not applicable — do not drop the status column (NR144 §7 decision)

ALTER TABLE workflow_sequence_items
    ADD COLUMN result_doc_id VARCHAR(191) DEFAULT NULL
        REFERENCES documents(doc_id) ON DELETE SET NULL;
-- Reverse lookup sequence items by result_doc_id (child doc -> sequence item)
CREATE INDEX IF NOT EXISTS idx_wfseq_items_result_doc
    ON workflow_sequence_items (result_doc_id);
-- Step 2: backfill each sequence item's latest registered result doc_id (idempotent: only NULL items are targeted)
UPDATE workflow_sequence_items
SET result_doc_id = (
    SELECT wir.registered_doc_id
    FROM workflow_item_results AS wir
    WHERE wir.item_id = workflow_sequence_items.id
      AND wir.registered_doc_id IS NOT NULL
    ORDER BY wir.id DESC
    LIMIT 1
)
WHERE result_doc_id IS NULL
  AND EXISTS (
    SELECT 1 FROM workflow_item_results wir2
    WHERE wir2.item_id = workflow_sequence_items.id
      AND wir2.registered_doc_id IS NOT NULL
);