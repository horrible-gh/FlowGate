-- 110_repair_tokens_after_base_dirty_rebuild.sql
-- Repair schema loss caused by historical 108_tokens_resolve_base_dirty_scope.sql.
-- 107 added failure_origin_review + two failure-origin columns, but 108 rebuilt tokens
-- from an older column list and dropped that state again. 109 then added source_access.
-- Historical migrations are immutable; this forward migration restores the union.

ALTER TABLE tokens
    ADD COLUMN IF NOT EXISTS failure_origin_target_run_id TEXT;
ALTER TABLE tokens
    ADD COLUMN IF NOT EXISTS failure_origin_before_marker TIMESTAMP;
ALTER TABLE tokens
    ADD COLUMN IF NOT EXISTS source_access TEXT;

DO $$
DECLARE
    r record;
BEGIN
    -- Replace only CHECK constraints that govern action_scope. Constraint names have varied
    -- across historical rebuilds, so discover them from the catalog rather than guessing.
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'tokens'
          AND c.contype = 'c'
          AND a.attname = 'action_scope'
    LOOP
        EXECUTE format('ALTER TABLE %I.tokens DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'tokens'
          AND c.conname = 'tokens_source_access_check'
    ) THEN
        ALTER TABLE tokens
            ADD CONSTRAINT tokens_source_access_check
            CHECK (source_access IN ('read', 'read_write'));
    END IF;
END $$;

ALTER TABLE tokens
    ADD CONSTRAINT tokens_action_scope_check
    CHECK (action_scope IN (
        'new', 'edit', 'workflow_decide', 'review', 'test_run',
        'workflow_sequence_edit', 'resolve_conflict', 'chat',
        'failure_origin_review', 'resolve_base_dirty'
    ));
