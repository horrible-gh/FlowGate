-- 185_chat_execute_mode.sql — flowgate.v02test.0026 (0001-B §7): the chat "execute" mode (PostgreSQL).
-- Additive only. tokens.command_access is NULL for every token but a chat token issued in execute mode
-- ('execute': it may run FlowGate commands while source_access stays 'read'); fixed at issue time, no
-- backfill. user_chat_source_access.source_access_mode admits 'execute': the CHECK that reads the column
-- is found in the catalog, dropped and added again with the list widened (the same shape as 166). Rows are
-- not touched; safe to run again.
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS command_access TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'tokens' AND c.conname = 'tokens_command_access_check'
    ) THEN
        ALTER TABLE tokens ADD CONSTRAINT tokens_command_access_check CHECK (command_access IN ('execute'));
    END IF;
END $$;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT DISTINCT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'user_chat_source_access'
          AND c.contype = 'c'
          AND a.attname = 'source_access_mode'
    LOOP
        EXECUTE format('ALTER TABLE %I.user_chat_source_access DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

ALTER TABLE user_chat_source_access
    ADD CONSTRAINT ck_ucsa_group_mode
    CHECK (source_access_mode IN ('read_only', 'execute', 'edit', 'edit_once'));

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised. Default rollback = revert the code only: an older server reads 'execute' as read_only
-- (resolve_source_access_mode's fail-closed domain check) and never reads tokens.command_access.
-- ── END DOWN ──────────────────────────────────────────────────────────────────
