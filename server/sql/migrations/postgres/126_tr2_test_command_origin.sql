-- 126: admit TR2-origin commands after successful approval finalization.
BEGIN;
ALTER TABLE project_test_commands DROP CONSTRAINT project_test_commands_origin_check;
ALTER TABLE project_test_commands ADD CONSTRAINT project_test_commands_origin_check
    CHECK (origin IN ('manual', 'auto', 'tr2'));
COMMIT;
