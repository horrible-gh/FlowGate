-- T299 R016 workflow decision system — reflects the DB002 correction
-- workflow_sequences: one sequence header per document (1:1)
-- workflow_sequence_items: sequence items (DS / D / P / L / DB / N / T / TS / V / C / M / NR / TR / TSR)
--
-- Reflecting the R016 correction:
--   - head_advanced_at: add the PM AC (group approval) timestamp
--   - doc_class: add the document classification (R/Q/B) column
--   - is_auto / auto_of_id: exclude automatic-mode columns (R016 correction — automatic mode will use a separate future R)

CREATE TABLE IF NOT EXISTS workflow_sequences (
    id               SERIAL PRIMARY KEY,
    doc_id           TEXT    NOT NULL UNIQUE,
    head_advanced_at TEXT             DEFAULT NULL,  -- PM AC (group approval) time. NULL = incomplete (ISO 8601 UTC)
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wfseq_doc_id
    ON workflow_sequences (doc_id);
-- Look up head completion status (head_advanced_at NULL means incomplete)
CREATE INDEX IF NOT EXISTS idx_wfseq_head_advanced
    ON workflow_sequences (head_advanced_at)
    WHERE head_advanced_at IS NULL;
CREATE TABLE IF NOT EXISTS workflow_sequence_items (
    id          SERIAL PRIMARY KEY,
    sequence_id INTEGER NOT NULL REFERENCES workflow_sequences(id) ON DELETE CASCADE,
    item_seq    INTEGER NOT NULL,          -- Instance counter within the sequence (1-based)
    type        TEXT    NOT NULL,          -- doc_type (DS/D/P/L/DB/N/T/TS/V/C/M/NR/TR/TSR)
    label       TEXT    NOT NULL,          -- Display name (Korean)
    doc_class   TEXT    NOT NULL,          -- Document classification (R/Q/B)
    sort_order  INTEGER NOT NULL,          -- Display order (0-based)
    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'done')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (sequence_id, item_seq)
);
-- Look up all sequence items (sorted by sort_order)
CREATE INDEX IF NOT EXISTS idx_wfseq_items_seq_order
    ON workflow_sequence_items (sequence_id, sort_order);
-- Extract the head (minimum sort_order among pending items)
CREATE INDEX IF NOT EXISTS idx_wfseq_items_seq_status_order
    ON workflow_sequence_items (sequence_id, status, sort_order);