-- Persist the value and unit separately; existing installations retain 7 days.
INSERT OR IGNORE INTO system_settings (setting_key, setting_value, value_type, description, updated_at) VALUES
    ('scratch_ttl_value', '7', 'integer', 'Scratch retention amount', datetime('now')),
    ('scratch_ttl_unit', 'day', 'string', 'Scratch retention unit', datetime('now'));
