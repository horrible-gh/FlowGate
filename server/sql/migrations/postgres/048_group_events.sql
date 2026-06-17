-- 048_group_events.sql
-- B0001 (group 0082): group-disposal / group-close 500 — FK constraint failure.
--
-- Root cause (NR0003): group-level terminal events (group_disposed / group_closed) were
-- written into the document-scoped `events` table with doc_id = group_id. But
-- `events.doc_id` is `NOT NULL REFERENCES documents(doc_id)`, and a group_id can never
-- equal a documents.doc_id (doc_id == "{group_id}.{doc_code}"). The violation was latent
-- while the live sqlite backend left foreign_keys OFF; once connection.transaction() began
-- forcing `PRAGMA foreign_keys = ON` (L0007 §9), it surfaced as a hard
-- sqlite3.IntegrityError → 500 on POST /groups/{group_id}/dispose (and the same defect in
-- close_group).
--
-- Fix (NR0003 preferred): move group-level events to a dedicated channel that references
-- `groups`, not `documents`. This resolves dispose AND close consistently — close has no
-- carrier document to anchor to, so anchoring to a document was never viable for it.
-- writer: process_service.dispose_group / _apply_group_terminal_action → db.insert_group_event
-- reader: process_service.get_group_detail → db.get_group_events
--
-- The loader globs *.sql in sorted order; 047 (conversation doctype) is the prior number.

CREATE TABLE IF NOT EXISTS group_events (
    event_id    SERIAL PRIMARY KEY,
    group_id    TEXT    NOT NULL REFERENCES groups(group_id),
    event_type  TEXT    NOT NULL,        -- group_disposed | group_closed
    reason      TEXT,                    -- normalized reason option value
    note        TEXT,                    -- rendered action note (label / time / detail)
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_events_group ON group_events(group_id, event_id DESC);