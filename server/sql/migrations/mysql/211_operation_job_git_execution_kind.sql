-- Durable manual Git sequences (flowgate.v02test.0068). Preserve all jobs and references.
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='operation_job' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%kind%' AND cc.CHECK_CLAUSE LIKE '%final_approval_publish%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE operation_job DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE operation_job ADD CONSTRAINT operation_job_kind_check CHECK (kind IN ('final_approval_publish', 'branch_merge_publish', 'worktree_provision', 'worktree_cleanup', 'base_publish', 'archive_preserve', 'archive_restore', 'archive_purge', 'project_provision', 'gate_publish', 'gate_cleanup', 'git_execution'));
