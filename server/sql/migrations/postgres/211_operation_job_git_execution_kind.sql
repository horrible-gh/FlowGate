-- Durable manual Git sequences (flowgate.v02test.0068). Preserve all jobs and references.
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
          AND t.relname = 'operation_job'
          AND c.contype = 'c'
          AND a.attname = 'kind'
    LOOP
        EXECUTE format('ALTER TABLE %I.operation_job DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

ALTER TABLE operation_job
    ADD CONSTRAINT operation_job_kind_check
    CHECK (kind IN ('final_approval_publish', 'branch_merge_publish', 'worktree_provision', 'worktree_cleanup', 'base_publish', 'archive_preserve', 'archive_restore', 'archive_purge', 'project_provision', 'gate_publish', 'gate_cleanup', 'git_execution'));
