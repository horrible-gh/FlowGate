-- 024_m026_review_workflow.sql
-- M026 Phase 0b — add the VR workflow step + add document review columns to documents
-- Add 'VR' to the workflow_sequence_items.type CHECK (SQLite recreation required)

CREATE TABLE workflow_sequence_items_new (
    id          SERIAL PRIMARY KEY,
    sequence_id INTEGER NOT NULL REFERENCES workflow_sequences(id) ON DELETE CASCADE,
    item_seq    INTEGER NOT NULL,
    type        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    doc_class   TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'done')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (sequence_id, item_seq)
);
INSERT INTO workflow_sequence_items_new SELECT * FROM workflow_sequence_items;
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "workflow_sequence_items"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_workflow_sequence_items ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('workflow_sequence_items')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('workflow_sequence_items')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
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

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "workflow_sequence_items"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_workflow_sequence_items') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_workflow_sequence_items LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
