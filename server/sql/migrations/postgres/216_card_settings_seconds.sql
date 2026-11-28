-- F81: canonical seconds settings and TS execution budgets.
BEGIN;
ALTER TABLE chat_card_setting_audit DROP CONSTRAINT IF EXISTS chat_card_setting_audit_field_check;
UPDATE chat_card_setting_audit SET before_json = CASE WHEN field IN ('time_limit', 'verify_run_minutes') AND jsonb_typeof(before_json::jsonb -> 'value') = 'number' THEN jsonb_set(before_json::jsonb, '{value}', to_jsonb((before_json::jsonb ->> 'value')::bigint * 60))::text ELSE before_json END, after_json = CASE WHEN field IN ('time_limit', 'verify_run_minutes') AND jsonb_typeof(after_json::jsonb -> 'value') = 'number' THEN jsonb_set(after_json::jsonb, '{value}', to_jsonb((after_json::jsonb ->> 'value')::bigint * 60))::text ELSE after_json END, field = CASE field WHEN 'time_limit' THEN 'time_limit_sec' WHEN 'verify_run_minutes' THEN 'verify_run_sec' ELSE field END WHERE field IN ('time_limit', 'verify_run_minutes');
ALTER TABLE chat_card_setting_audit ADD CONSTRAINT chat_card_setting_audit_field_check CHECK (field IN ('review_count', 'restart', 'time_limit_sec', 'verify_max_runs', 'verify_run_sec', 'auto_case_timeout_sec', 'ai_judge_mode', 'ai_judge_run_sec', 'ai_judge_runs'));
INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by)
SELECT group_id, node_key, 'time_limit_sec', CAST(GREATEST(1800, LEAST(14400, CAST(value_json AS BIGINT) * 60)) AS TEXT), updated_at, updated_by FROM graph_node_settings WHERE field = 'time_limit' AND value_json <> 'null'
ON CONFLICT (group_id, node_key, field) DO NOTHING;
DELETE FROM graph_node_settings WHERE field = 'time_limit';
INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by)
SELECT group_id, node_key, 'verify_run_sec', CAST(GREATEST(60, LEAST(1800, CAST(value_json AS BIGINT) * 60)) AS TEXT), updated_at, updated_by FROM graph_node_settings WHERE field = 'verify_run_minutes' AND value_json <> 'null'
ON CONFLICT (group_id, node_key, field) DO NOTHING;
DELETE FROM graph_node_settings WHERE field = 'verify_run_minutes';
COMMIT;
