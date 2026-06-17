-- 042_project_messages.sql
-- R0001 (group 0004): project-custom messages + mention-add feature.
-- Backing store for the Settings > Project Management > Message Management screen CRUD and the
-- mention-copy dialog query. [All] is stored as doc_type='*' (DB0008 §3).
-- Display merge/sort/fallback is L0007's concern.
--
-- NOTE: DB0008 §5 specified migration 041, but 041 was taken by 041_remote_tool.sql
-- on the dev trunk (group 0003). Next free number is 042.

BEGIN;

CREATE TABLE IF NOT EXISTS project_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL
                    REFERENCES projects(project_id) ON DELETE CASCADE,
    doc_type    TEXT    NOT NULL,                       -- document-type code or '*' ([All])
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_project_messages_lookup
    ON project_messages(project, doc_type);

COMMIT;
