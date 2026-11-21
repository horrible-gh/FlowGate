-- 201_chat_card_settings_audit_verify_fields.sql (postgres)
-- flowgate.v02test.0053 0003-T (1-A): chat_card_setting_audit.field also accepts the two verification options
-- (verify_max_runs, verify_run_minutes). The 191 inline column CHECK has the default name below. See the sqlite file.
-- DOWN: swap back to CHECK (field IN ('review_count', 'restart', 'time_limit')) after the verify_* rows are gone (not automatic).
BEGIN;
ALTER TABLE chat_card_setting_audit
  DROP CONSTRAINT IF EXISTS chat_card_setting_audit_field_check;
ALTER TABLE chat_card_setting_audit
  ADD CONSTRAINT chat_card_setting_audit_field_check
  CHECK (field IN ('review_count', 'restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes'));
COMMIT;
