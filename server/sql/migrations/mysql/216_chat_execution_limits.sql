-- F108-5: preserve existing chat durations and add per-user command limits.
ALTER TABLE user_conversation_settings ADD COLUMN timeout_seconds INTEGER NULL CHECK (timeout_seconds BETWEEN 60 AND 604800);
ALTER TABLE user_conversation_settings ADD COLUMN command_timeout_seconds INTEGER NULL CHECK (command_timeout_seconds BETWEEN 0 AND 1800);
UPDATE user_conversation_settings SET timeout_seconds = timeout_minutes * 60;
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='chat_command_requests' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%timeout_seconds%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE chat_command_requests DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE chat_command_requests ADD CONSTRAINT chat_command_requests_timeout_seconds_check CHECK (timeout_seconds >= 0);
