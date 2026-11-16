-- 190_chat_card_roles.sql — flowgate.v02gui.0028: the chat card AI role API (flowgate.chat_control.card_roles).
-- Additive only; nothing is backfilled: a card placed before this migration has no placement record, so no chat
-- may read or change its roles through the chat API (the user's card settings screen still can).
--   chat_placed_cards     one row per work card a chat placed (chat_control.service.place, after its draft save):
--                         the chat conversation document (CH doc_ref of the chat token) that placed it, the token
--                         and AI run of that call, the user, when. Only that CH's chat may use
--                         /api/v2/chat/flows/{flow_id}/nodes/{node_key}/roles on the card. Inserted only.
--   chat_card_role_audit  one row per requested role of every successful role PATCH of that API: token, AI run, CH,
--                         acting user, card, role, the role's state before / after (JSON), changed, settings version.
--                         Inserted only.
BEGIN;

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

-- SQLite only (163's pattern): FK-independent cleanup for hard deletes run with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_chat_placed_cards_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM chat_placed_cards WHERE group_id = OLD.group_id; END;
CREATE TRIGGER IF NOT EXISTS trg_chat_card_role_audit_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM chat_card_role_audit WHERE group_id = OLD.group_id; END;

COMMIT;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- Triggers first: they sit on groups and survive DROP TABLE.
-- DROP TRIGGER IF EXISTS trg_chat_placed_cards_group_deleted;
-- DROP TRIGGER IF EXISTS trg_chat_card_role_audit_group_deleted;
-- DROP TABLE IF EXISTS chat_card_role_audit;
-- DROP TABLE IF EXISTS chat_placed_cards;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
