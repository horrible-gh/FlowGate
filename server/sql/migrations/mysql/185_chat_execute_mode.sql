-- 185_chat_execute_mode.sql — flowgate.v02test.0026 (0001-B §7): the chat "execute" mode (MySQL/MariaDB)
-- Additive only. tokens.command_access is NULL for every token but a chat token issued in execute mode
-- ('execute': it may run FlowGate commands while source_access stays 'read'); fixed at issue time, no
-- backfill. user_chat_source_access.source_access_mode admits 'execute': the CHECK that holds the value
-- list is found by name, dropped and added again with the new value (the same shape as 166). Rows are not
-- touched.
ALTER TABLE tokens
    ADD COLUMN command_access TEXT
        CHECK (command_access IN ('execute'));

SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='user_chat_source_access' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%source_access_mode%' AND cc.CHECK_CLAUSE LIKE '%edit_once%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE user_chat_source_access DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE user_chat_source_access ADD CONSTRAINT ck_ucsa_group_mode CHECK (source_access_mode IN ('read_only', 'execute', 'edit', 'edit_once'));

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised. Default rollback = revert the code only: an older server reads 'execute' as read_only
-- (resolve_source_access_mode's fail-closed domain check) and never reads tokens.command_access.
-- ── END DOWN ──────────────────────────────────────────────────────────────────
