-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 recommendation P2 — per-query cost.
--
-- Background: NR0003 findings 2/6. Several documents/events queries on the screen refresh path
-- perform full scans rather than using indexes. Call counts are constant, but cost grows with
-- accumulated rows, causing long-term degradation even after fan-out (P1) is addressed.
--
-- This migration adds only indexes, leaving tables, columns, and data untouched.
-- Rollback therefore requires just five DROP INDEX statements.
--
-- ── documents ────────────────────────────────────────────────────────────────
--
-- No existing index has group_id as its leading column.
-- idx_documents_prj_mod_grp_status is (project_id, module, group_id, status), so
-- group_id is third; a query supplying only group_id without project_id/module
-- cannot use it. Three kinds of such queries occur on the screen refresh path.
--
-- 1) idx_documents_group_seq (group_id, seq)
--    db/workflow_return_points.py get_front_doc():
--        SELECT ... FROM documents WHERE group_id = ? AND seq = ?
--    _return_point_payload() runs two or three times per screen refresh, hence three log entries.
--
-- 2) idx_documents_group_type_review (group_id, type_code, doc_review_status)
--    Covers both batch queries in services/git_service.py:
--        _groups_root_wf_done():  WHERE group_id IN (...) AND type_code IN ('R','B')
--                                   AND doc_review_status = 'wf_done'
--        _group_ac_doc_ids():     WHERE group_id IN (...) AND type_code = 'AC'
--                                   GROUP BY group_id
--    The single-group variants (_group_root_wf_done / _group_ac_doc_id) have the same shape.
--    Column order follows predicate selectivity: narrow by group_id, reduce to single digits by type_code,
--    and filter doc_review_status within the index to eliminate table access.
--
-- 3) idx_documents_prj_group_updated (project_id, group_id, updated_at DESC)
--    db/documents.py get_documents():
--        WHERE project_id = ? AND group_id = ? [AND type_code = ?]
--        ORDER BY updated_at DESC LIMIT ?
--    Previously only the project_id prefix used an index, with filesort for ordering. This index also
--    contains sort order, turning LIMIT into early termination: rows read remain near LIMIT
--    as group documents grow. DESC follows the existing idx_documents_updated convention
--    (SQLite/PostgreSQL/MySQL 8 store the direction; MariaDB parses and ignores it;
--     either way, a forward scan yields the same order, so behavior is unchanged).
--
-- 4) idx_documents_doc_type_status (type_code, status) — restore
--    015_phase3_qa.sql created this index, but **it currently exists in no DB.**
--    027_t487_workflow_review_status.sql rebuilt documents with DROP/RENAME,
--    recreating only the seven indexes from 001 and omitting two added by 013/015. Applying
--    all migrations and querying sqlite_master confirmed that a newly created DB retains
--    only the seven documents indexes from 001. The current-index list in NR0003 finding 2
--    was assembled from migration files and incorrectly lists these two as present.
--    db/documents.py get_documents_by_status_and_types()
--        (WHERE status = ? AND type_code IN (...)) was the original need for this index,
--    so restore it here.
--    Do **not** restore idx_documents_revision (doc_id, revision_no) from 013:
--    ux_documents_doc_id makes doc_id UNIQUE, leaving one row per doc_id and nothing
--    for the trailing revision_no to filter. Restoring it would only add write cost.
--
-- ── events ───────────────────────────────────────────────────────────────────
--
-- 5) idx_events_type_doc_event (event_type, doc_id, event_id)
--    NR0003 finding 6 / P2-2. The internal aggregation in db/events.py get_created_memo_files_map_by_project()
--    shown below
--        SELECT e2.doc_id, MAX(e2.event_id) FROM events e2
--        JOIN documents d ON d.doc_id = e2.doc_id
--        WHERE e2.event_type = 'created' AND ... GROUP BY e2.doc_id
--    runs on every tree response. Since events grow monotonically over a document's lifetime,
--    this query degrades fastest. Existing idx_events_type contains only event_type and cannot
--    help GROUP BY/MAX, requiring all event_type='created' rows to be read from the table again. This index
--    already orders groups by doc_id, with MAX(event_id) as each group's last entry,
--    so aggregation completes with an index scan.
--    memo_file is omitted from the index: MySQL cannot index a TEXT column without a prefix,
--    preventing a shared definition across all three dialects. The other NR0003 P2-2 option,
--    denormalizing documents (latest_created_memo_file), requires backfill and dual writes,
--    so it was not chosen in this round. Narrowing the scan to event_type='created' through
--    the index permits remeasurement before considering that option.
--
-- Write cost: four additional documents indexes (three new, one restored) and one events index.
-- Both tables are overwhelmingly read-heavy (writes are single rows at registration/state transitions),
-- and finding 1's fan-out repeats reads per connected user. Favoring reads is appropriate.

BEGIN;

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

COMMIT;
