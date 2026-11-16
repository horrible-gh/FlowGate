-- 191_chat_card_settings_audit.sql — flowgate.v02gui.0032 (0002-CH seq 15-17): the run settings a chat may change on the
-- cards it placed (flowgate.chat_control.run_settings: review count, restarts, time limit).
-- Additive only; nothing is backfilled.
--   chat_card_setting_audit  one row per requested setting of every successful chat change of those settings (the card
--                            role PATCH's `settings`, a placement's `settings`): token, AI run, CH, acting user, card,
--                            field, the shown value before / after (JSON), changed, settings version. Inserted only.
--                            chat_card_role_audit keeps the three AI roles (its role CHECK stays as it is).
BEGIN;

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

-- SQLite only (163's pattern): FK-independent cleanup for hard deletes run with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_chat_card_setting_audit_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM chat_card_setting_audit WHERE group_id = OLD.group_id; END;

COMMIT;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- Trigger first: it sits on groups and survives DROP TABLE.
-- DROP TRIGGER IF EXISTS trg_chat_card_setting_audit_group_deleted;
-- DROP TABLE IF EXISTS chat_card_setting_audit;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
