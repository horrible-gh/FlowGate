-- T231 Phase 3 QA Helper DB migration
-- Basis: D022 §4-7
-- Applied:
--   1. Add the documents(type_code, status) composite index (D022 §7-3)
--   2. Register Q/A document types (document_types)
--
-- Note: documents.status requires the 'answered' value.
--   New DBs already include 'answered' in 001_flowgate_schema.sql.
--   For existing production DBs, run the manual_upgrade.sql below separately.
--   (SQLite CHECK constraints cannot be altered directly — table recreation is required.)

-- D022 §7-3: (type_code, status) composite index
-- Optimize lookups for type_code='Q' AND status='open'
CREATE INDEX IF NOT EXISTS idx_documents_doc_type_status
    ON documents (type_code, status);
-- Register Q/A document types (system-global types — project_id NULL)
DO $fg_or_ignore$
BEGIN
INSERT INTO document_types
    (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at)
VALUES
    (NULL, 'Q', 'Question', 'qna', 1, 1, 90, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'A', 'Answer',   'qna', 1, 1, 91, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;