-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 recommendation P2 — per-query cost.
--
-- MySQL/MariaDB version of the SQLite original (sql/migrations/sqlite/070_documents_events_perf_indexes.sql).
-- See the original comments for rationale, column order, and the queries each index covers.
--
-- Only two dialect differences are addressed here.
--
-- 1. Remove BEGIN/COMMIT: DDL commits implicitly, making explicit transactions meaningless.
--    This follows existing generated migrations such as 069/068.
--
-- 2. TEXT column prefix length: documents.doc_review_status is TEXT in this dialect and
--    cannot be indexed without a prefix length (error 1170). Use 191, following
--    `revoked_at(191)` in 067a_auth_sessions.sql. 191 is the VARCHAR width this schema
--    uses for string keys; the longest actual doc_review_status value is
--    'wf_in_progress' (14 characters), so no truncation occurs.
--    All remaining columns are VARCHAR/INTEGER and are used unchanged:
--      documents.group_id VARCHAR(191) / type_code VARCHAR(191) / project_id VARCHAR(191)
--      documents.updated_at VARCHAR(191) / status VARCHAR(64) / seq INTEGER
--      events.event_type VARCHAR(191) / doc_id VARCHAR(191) / event_id INTEGER
--    the largest key, idx_documents_group_type_review, is (764+764+764)=2292 bytes,
--    within the InnoDB DYNAMIC row format limit of 3072.
--
-- MariaDB parses and ignores the ORDER BY direction (DESC). A forward scan produces the same order,
-- so behavior is unchanged. This also matches the notation already used by idx_events_doc_created in 001
-- and idx_documents_updated in 027.

CREATE INDEX IF NOT EXISTS idx_documents_group_seq
    ON documents(group_id, seq);

CREATE INDEX IF NOT EXISTS idx_documents_group_type_review
    ON documents(group_id, type_code, doc_review_status(191));

CREATE INDEX IF NOT EXISTS idx_documents_prj_group_updated
    ON documents(project_id, group_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type_status
    ON documents(type_code, status);

CREATE INDEX IF NOT EXISTS idx_events_type_doc_event
    ON events(event_type, doc_id, event_id);
