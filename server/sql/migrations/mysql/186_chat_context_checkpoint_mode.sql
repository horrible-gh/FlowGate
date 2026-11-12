-- 186_chat_context_checkpoint_mode.sql  (mysql)
-- flowgate.v02test.0025: the [checkpoint] context mode.
--   user_chat_settings.context_mode  accepts 'checkpoint' (the chat's checkpoint decides the window; the last 5
--                                    turns without one). 'recent' and 'all' are unchanged.
--   user_conversation_settings.context_checkpoint_kind
--                                    'include' = send from the checkpoint message, 'exclude' = from the next
--                                    message. NULL reads as 'include' (checkpoints saved under 185).
-- The CHECK that governs user_chat_settings.context_mode is found by name, dropped and added again with the new
-- value (the same shape as 159). Rows are not touched.
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='user_chat_settings' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%context_mode%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE user_chat_settings DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE user_chat_settings ADD CONSTRAINT user_chat_settings_context_mode_check CHECK (context_mode IN ('recent', 'all', 'checkpoint'));

ALTER TABLE user_conversation_settings ADD COLUMN context_checkpoint_kind VARCHAR(16) NULL;
