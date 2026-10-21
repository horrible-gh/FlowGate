-- 157_chat_command_output_truncation.sql
-- flowgate.v02.0081: chat_command_requests.stdout_truncated / stderr_truncated record that
-- the stored 64 KiB tail is already shorter than what the process wrote.
-- The table itself comes from main's 136_chat_command_execution.sql. v0.2 used to carry its
-- own copy of that file and of 137_chat_activity_anchor.sql (as 137/138), which re-created
-- the same table, indexes and columns and stopped every boot on
-- "Duplicate key name 'idx_chat_cmd_run'". Those copies are gone; these two columns were
-- the only thing they added. IF NOT EXISTS: a DB that ran v0.2's 137 between 10-06 and 10-08
-- already has them (its 137/138 rows are carried over to 136/137 by migration_rename_repair).

ALTER TABLE chat_command_requests ADD COLUMN IF NOT EXISTS stdout_truncated INTEGER NOT NULL DEFAULT 0
    CHECK (stdout_truncated IN (0, 1));
ALTER TABLE chat_command_requests ADD COLUMN IF NOT EXISTS stderr_truncated INTEGER NOT NULL DEFAULT 0
    CHECK (stderr_truncated IN (0, 1));
