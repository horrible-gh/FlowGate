-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 recommendation P2 — per-query cost.
--
-- PostgreSQL version of the SQLite original (sql/migrations/sqlite/070_documents_events_perf_indexes.sql).
-- See the original comments for rationale, column order, and the queries each index covers.
--
-- The only dialect difference is removing BEGIN/COMMIT (as in existing generated migrations). PostgreSQL keeps
-- all string columns in this schema as TEXT, requires no prefix length for TEXT indexes
-- (unlike MySQL), and supports both DESC index-column direction and CREATE INDEX IF NOT EXISTS.
-- Therefore the SQLite original's DDL is used unchanged.

CREATE INDEX IF NOT EXISTS idx_documents_group_seq
    ON documents(group_id, seq);

CREATE INDEX IF NOT EXISTS idx_documents_group_type_review
    ON documents(group_id, type_code, doc_review_status);

CREATE INDEX IF NOT EXISTS idx_documents_prj_group_updated
    ON documents(project_id, group_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type_status
    ON documents(type_code, status);

CREATE INDEX IF NOT EXISTS idx_events_type_doc_event
    ON events(event_type, doc_id, event_id);
