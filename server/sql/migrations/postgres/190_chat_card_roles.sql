-- 190_chat_card_roles.sql  (postgres)
-- flowgate.v02gui.0028: the chat card AI role API (flowgate.chat_control.card_roles). See the sqlite file.
--   chat_placed_cards     one row per work card a chat placed: the CH conversation document that placed it.
--   chat_card_role_audit  one row per requested role of every successful role PATCH of the chat API.

CREATE TABLE IF NOT EXISTS chat_placed_cards (
    group_id     TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key     TEXT NOT NULL,
    chat_doc_id  TEXT NOT NULL,
    token_id     TEXT,
    ai_run_id    TEXT,
    placed_by    TEXT,
    placed_at    TEXT NOT NULL,
    PRIMARY KEY (group_id, node_key)
);

CREATE INDEX IF NOT EXISTS idx_chat_placed_cards_chat ON chat_placed_cards(group_id, chat_doc_id);

CREATE TABLE IF NOT EXISTS chat_card_role_audit (
    audit_id          TEXT NOT NULL PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key          TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('instruction', 'execution', 'review')),
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

CREATE INDEX IF NOT EXISTS idx_chat_card_role_audit_card ON chat_card_role_audit(group_id, node_key, created_at);

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS chat_card_role_audit;
-- DROP TABLE IF EXISTS chat_placed_cards;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
