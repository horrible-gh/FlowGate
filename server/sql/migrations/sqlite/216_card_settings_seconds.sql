-- F81: canonical seconds settings and TS execution budgets.
PRAGMA foreign_keys = OFF;
BEGIN;
DROP TRIGGER IF EXISTS trg_chat_card_setting_audit_group_deleted;
CREATE TABLE chat_card_setting_audit_new (
    audit_id          TEXT NOT NULL PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key          TEXT NOT NULL,
    field             TEXT NOT NULL CHECK (field IN ('review_count', 'restart', 'time_limit_sec', 'verify_max_runs', 'verify_run_sec', 'auto_case_timeout_sec', 'ai_judge_mode', 'ai_judge_run_sec', 'ai_judge_runs')),
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
SELECT audit_id, group_id, node_key, CASE field WHEN 'time_limit' THEN 'time_limit_sec' WHEN 'verify_run_minutes' THEN 'verify_run_sec' ELSE field END, CASE WHEN field IN ('time_limit', 'verify_run_minutes') AND json_type(before_json, '$.value') = 'integer' THEN json_set(before_json, '$.value', json_extract(before_json, '$.value') * 60) ELSE before_json END, CASE WHEN field IN ('time_limit', 'verify_run_minutes') AND json_type(after_json, '$.value') = 'integer' THEN json_set(after_json, '$.value', json_extract(after_json, '$.value') * 60) ELSE after_json END, changed, settings_version, token_id, ai_run_id,
       chat_doc_id, actor_user_id, created_at
FROM chat_card_setting_audit;
DROP TABLE chat_card_setting_audit;
ALTER TABLE chat_card_setting_audit_new RENAME TO chat_card_setting_audit;
CREATE INDEX IF NOT EXISTS idx_chat_card_setting_audit_card ON chat_card_setting_audit(group_id, node_key, created_at);
CREATE TRIGGER IF NOT EXISTS trg_chat_card_setting_audit_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM chat_card_setting_audit WHERE group_id = OLD.group_id; END;
INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by)
SELECT group_id, node_key, 'time_limit_sec', CAST(MAX(1800, MIN(14400, CAST(value_json AS INTEGER) * 60)) AS TEXT), updated_at, updated_by FROM graph_node_settings WHERE field = 'time_limit' AND value_json <> 'null'
ON CONFLICT (group_id, node_key, field) DO NOTHING;
DELETE FROM graph_node_settings WHERE field = 'time_limit';
INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by)
SELECT group_id, node_key, 'verify_run_sec', CAST(MAX(60, MIN(1800, CAST(value_json AS INTEGER) * 60)) AS TEXT), updated_at, updated_by FROM graph_node_settings WHERE field = 'verify_run_minutes' AND value_json <> 'null'
ON CONFLICT (group_id, node_key, field) DO NOTHING;
DELETE FROM graph_node_settings WHERE field = 'verify_run_minutes';
COMMIT;
PRAGMA foreign_keys = ON;
