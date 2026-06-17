-- T303 R016 T-E: workflow result registration — workflow_item_results table
--
-- When a worker submits an output file, a pending_approval record is created in this table.
-- On PM AC (approval), status -> approved and the sequence item is marked done.
-- On PM RJ (rejection), status -> rejected and the sequence item stays in_progress (resubmission allowed).

CREATE TABLE IF NOT EXISTS workflow_item_results (
    id                INTEGER PRIMARY KEY AUTO_INCREMENT,
    item_id           INTEGER NOT NULL REFERENCES workflow_sequence_items(id) ON DELETE CASCADE,
    registered_path   TEXT    NOT NULL,   -- Canonical storage path (absolute path based on docs_root)
    registered_doc_id TEXT    NOT NULL,   -- doc_number from the YAML header (example: L003)
    status            VARCHAR(191)    NOT NULL DEFAULT 'pending_approval'
                          CHECK (status IN ('pending_approval', 'approved', 'rejected')),
    registered_at     TEXT    NOT NULL,   -- Submission timestamp (ISO 8601 UTC)
    reviewed_at       TEXT             DEFAULT NULL,   -- AC/RJ processing time
    reviewed_by       TEXT             DEFAULT NULL,   -- User ID that processed AC/RJ
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_item_results_item_id
    ON workflow_item_results (item_id);
CREATE INDEX IF NOT EXISTS idx_wf_item_results_status
    ON workflow_item_results (item_id, status);