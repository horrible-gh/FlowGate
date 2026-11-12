-- 186_chat_context_checkpoint_mode.sql  (postgres)
-- flowgate.v02test.0025: the [checkpoint] context mode.
--   user_chat_settings.context_mode  accepts 'checkpoint' (the chat's checkpoint decides the window; the last 5
--                                    turns without one). 'recent' and 'all' are unchanged.
--   user_conversation_settings.context_checkpoint_kind
--                                    'include' = send from the checkpoint message, 'exclude' = from the next
--                                    message. NULL reads as 'include' (checkpoints saved under 185).
-- The CHECK that governs user_chat_settings.context_mode is found in the catalog, dropped and added again with
-- the new value (the same shape as 159). Rows are not touched; safe to run again.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'user_chat_settings'
          AND c.contype = 'c'
          AND a.attname = 'context_mode'
    LOOP
        EXECUTE format('ALTER TABLE %I.user_chat_settings DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

ALTER TABLE user_chat_settings
    ADD CONSTRAINT user_chat_settings_context_mode_check
    CHECK (context_mode IN ('recent', 'all', 'checkpoint'));

ALTER TABLE user_conversation_settings ADD COLUMN IF NOT EXISTS context_checkpoint_kind TEXT;
