-- 201_chat_card_settings_audit_verify_fields.sql (sqlite) — flowgate.v02test.0053 0003-T (1-A): a chat may now also change a run card's two verification options
-- (flowgate.chat_control.run_settings: verify_max_runs, verify_run_minutes), so chat_card_setting_audit (191) must accept
-- them as its `field`. SQLite cannot alter a CHECK in place: rebuild the 191 shape (data, foreign key, index kept) with
-- only that one CHECK widened. The trigger on groups goes first and comes back last: ALTER TABLE ... RENAME re-parses
-- every trigger, and one whose body names the dropped table would fail it.
-- DOWN: rebuild with CHECK (field IN ('review_count', 'restart', 'time_limit')) after the verify_* rows are gone (not automatic).
PRAGMA foreign_keys = OFF;
BEGIN;
DROP TRIGGER IF EXISTS trg_chat_card_setting_audit_group_deleted;
CREATE TABLE chat_card_setting_audit_new (
    audit_id          TEXT NOT NULL PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key          TEXT NOT NULL,
    field             TEXT NOT NULL CHECK (field IN ('review_count', 'restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes')),
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
INSERT INTO chat_card_setting_audit_new
    (audit_id, group_id, node_key, field, before_json, after_json, changed, settings_version, token_id, ai_run_id,
     chat_doc_id, actor_user_id, created_at)
SELECT audit_id, group_id, node_key, field, before_json, after_json, changed, settings_version, token_id, ai_run_id,
       chat_doc_id, actor_user_id, created_at
FROM chat_card_setting_audit;
DROP TABLE chat_card_setting_audit;
ALTER TABLE chat_card_setting_audit_new RENAME TO chat_card_setting_audit;
CREATE INDEX IF NOT EXISTS idx_chat_card_setting_audit_card ON chat_card_setting_audit(group_id, node_key, created_at);
CREATE TRIGGER IF NOT EXISTS trg_chat_card_setting_audit_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM chat_card_setting_audit WHERE group_id = OLD.group_id; END;
COMMIT;
PRAGMA foreign_keys = ON;
