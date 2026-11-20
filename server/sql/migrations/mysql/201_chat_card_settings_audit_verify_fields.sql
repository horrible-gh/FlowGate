-- 201_chat_card_settings_audit_verify_fields.sql (mysql)
-- flowgate.v02test.0053 0003-T (1-A): chat_card_setting_audit.field also accepts the two verification options
-- (verify_max_runs, verify_run_minutes). 191 named the CHECK ck_ccsa_field. See the sqlite file.
-- DOWN: swap back to CHECK (field IN ('review_count', 'restart', 'time_limit')) after the verify_* rows are gone (not automatic).
ALTER TABLE chat_card_setting_audit DROP CHECK ck_ccsa_field;
ALTER TABLE chat_card_setting_audit ADD CONSTRAINT ck_ccsa_field CHECK (field IN ('review_count', 'restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes'));
