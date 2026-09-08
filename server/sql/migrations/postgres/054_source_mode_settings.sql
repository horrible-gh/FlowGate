-- 054_source_mode_settings.sql
-- Local/remote source-mode settings. Global default stays remote; project override
-- maps to the existing project_settings row.

ALTER TABLE project_settings ADD COLUMN source_mode_override TEXT;
DO $fg_or_ignore$
BEGIN
INSERT INTO system_settings(setting_key, setting_value, value_type, description, updated_at)
VALUES('source_mode', 'remote', 'string', 'Default project source access mode', CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;