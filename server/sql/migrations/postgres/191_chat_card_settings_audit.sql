-- 191_chat_card_settings_audit.sql  (postgres)
-- flowgate.v02gui.0032: the run settings a chat may change on the cards it placed. See the sqlite file.
--   chat_card_setting_audit  one row per requested setting of every successful chat change of those settings.

CREATE TABLE IF NOT EXISTS chat_card_setting_audit (
    audit_id          TEXT NOT NULL PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key          TEXT NOT NULL,
    field             TEXT NOT NULL CHECK (field IN ('review_count', 'restart', 'time_limit')),
    before_json       TEXT,
    after_json        TEXT,
    changed           INTEGER NOT NULL CHECK (changed IN (0, 1)),
    settings_version  INTEGER,
    token_id          TEXT,
    ai_run_id         TEXT,
    chat_doc_id       TEXT NOT NULL,
    actor_user_id     TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_card_setting_audit_card ON chat_card_setting_audit(group_id, node_key, created_at);

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS chat_card_setting_audit;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
