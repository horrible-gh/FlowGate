-- 054_source_mode_settings.sql
-- Local/remote source-mode settings. Global default stays remote; project override
-- maps to the existing project_settings row.

ALTER TABLE project_settings ADD COLUMN source_mode_override TEXT;

INSERT OR IGNORE INTO system_settings(setting_key, setting_value, value_type, description, updated_at)
VALUES('source_mode', 'remote', 'string', 'Default project source access mode', datetime('now'));
