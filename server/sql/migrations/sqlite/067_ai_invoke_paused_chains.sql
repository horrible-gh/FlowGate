-- 067_ai_invoke_paused_chains.sql
-- User-paused continuous chains (miniplayer pause/resume — group 0252 DB0010).
-- One row = one paused continuous chain; UNIQUE(group_id) is the upsert key and
-- enforces at most one paused row per group. FKs cascade so a discarded group,
-- deleted spine document, or removed user can never leave a ghost paused card.

BEGIN;

CREATE TABLE IF NOT EXISTS ai_invoke_paused_chains (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id                TEXT    NOT NULL UNIQUE REFERENCES groups(group_id) ON DELETE CASCADE,
    doc_ref                 TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    mode                    TEXT    NOT NULL DEFAULT ('continuous') CHECK (mode = 'continuous'),
    paused_by               TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    paused_at               TEXT    NOT NULL,
    continuation_target_seq INTEGER,
    docs_target             INTEGER,
    docs_reached            INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_aipc_paused_by
    ON ai_invoke_paused_chains(paused_by);

COMMIT;
