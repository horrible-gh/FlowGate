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
    CHECK (context_mode IN ('recent', 'all', 'checkpoint', 'checkpoint_recent'));

ALTER TABLE user_chat_settings ADD COLUMN checkpoint_max_turns INTEGER NOT NULL DEFAULT 10;

