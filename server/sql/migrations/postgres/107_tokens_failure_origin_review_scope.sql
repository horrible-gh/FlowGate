-- 104_tokens_failure_origin_review_scope.sql
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
          AND t.relname = 'tokens'
          AND c.contype = 'c'
          AND a.attname = 'action_scope'
    LOOP
        EXECUTE format('ALTER TABLE %I.tokens DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;
ALTER TABLE tokens ADD CONSTRAINT tokens_action_scope_check
CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict', 'chat', 'failure_origin_review'));
ALTER TABLE tokens ADD COLUMN failure_origin_target_run_id TEXT;
ALTER TABLE tokens ADD COLUMN failure_origin_before_marker TIMESTAMP;
